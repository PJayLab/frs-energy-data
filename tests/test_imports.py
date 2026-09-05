"""Import regression tests; SQLite exercises real async SQLAlchemy sessions.

PostGIS/JSONB storage is substituted only in this test process.
"""
import unittest
import os
import uuid
import asyncio
from io import BytesIO
from unittest.mock import patch

from sqlalchemy import JSON, Text, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.types import TypeDecorator

from app.models import Object, ServiceConnection
from app.schemas import GPSImportData, ImportData
from app import services
from app.api.import_router import import_excel
from fastapi import UploadFile
from openpyxl import Workbook


class GeometryText(TypeDecorator):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return str(value) if value is not None else None


def row(objects='Chäppelimatt 5', notes='PVA: 30 kVA', municipality='A'):
    return (municipality, objects, None, notes, None, None, None, 'Transformer A', '4')


class ParsingTests(unittest.TestCase):
    def test_single(self):
        self.assertEqual(services.normalize_row(row())[0]['connection_notes'], ['PVA: 30 kVA'])

    def test_equal_notes(self):
        entries = services.normalize_row(row('Haus 1\nHaus 2', 'Note 1\nNote 2'))
        self.assertEqual([e['connection_notes'] for e in entries], [['Note 1'], ['Note 2']])

    def test_positional_blanks(self):
        entries = services.normalize_row(row('Haus 1\nHaus 2\nHaus 3', '\n\nLadestation: 11 kVA'))
        self.assertEqual([e['connection_notes'] for e in entries], [[], [], ['Ladestation: 11 kVA']])

    def test_blank_object_and_insurance_positions(self):
        data = list(row('Haus 1\n\nHaus 3', 'First\n\nThird'))
        data[2] = '0333\n\n0335'
        entries = services.normalize_row(data)
        self.assertEqual([e['insurance_number'] for e in entries], ['0333', '0335'])
        self.assertEqual(entries[1]['connection_notes'], ['Third'])

    def test_fallbacks(self):
        technical = services.normalize_row(row('A\nB', 'PVA\nx\ny'))
        self.assertEqual(technical[1]['connection_notes'], ['PVA', 'x', 'y'])
        ordinary = services.normalize_row(row('A\nB', 'x\ny\nz'))
        self.assertEqual(ordinary[1]['connection_notes'], ['y'])
        self.assertTrue(ordinary[0]['_warnings'])

    def test_zero_crlf_nbsp(self):
        self.assertEqual(services.split_positional('\r\nx\xa0y\r\n'), ['', 'x y'])
        self.assertEqual(services.split_compact(0), ['0'])

    def test_multiple_source_edit_overrides_cached_names(self):
        self.assertEqual(services.source_values(dict(source_name='A\nB', source_names=['A'])), ['A', 'B'])


class ImportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.types = [(Object.__table__.c.geom, Object.__table__.c.geom.type)]
        Object.__table__.c.geom.type = GeometryText()
        for name in ('source_outgoing', 'disconnect_point_outgoing', 'connection_notes'):
            column = ServiceConnection.__table__.c[name]
            self.types.append((column, column.type))
            column.type = JSON()
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda c: Object.__table__.create(c))
            await conn.run_sync(lambda c: ServiceConnection.__table__.create(c))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session = self.sessions()
        self.session.add(Object(name='TransformerA', friendly_name='Transformer A', type='transformer'))
        await self.session.commit()

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()
        for column, original in self.types:
            column.type = original

    async def objects(self):
        return list((await self.session.scalars(select(Object))).all())

    async def connections(self):
        return list((await self.session.scalars(select(ServiceConnection))).all())

    async def test_reimport_and_display_name(self):
        entries = services.normalize_row(row())
        first = await services.import_service_connections(entries, self.session)
        second = await services.import_service_connections(entries, self.session)
        self.assertEqual(first['created'], 1)
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['unchanged'], 1)
        self.assertEqual(len(await self.objects()), 2)
        connections = await self.connections()
        self.assertEqual(len(connections), 1)
        self.assertEqual(connections[0].connection_notes, ['PVA: 30 kVA'])
        building = next(o for o in await self.objects() if o.type == 'building')
        self.assertEqual((building.name, building.friendly_name), ('Chäppelimatt5', 'Chäppelimatt 5'))

    async def test_edit(self):
        entries = services.normalize_row(row())
        await services.import_service_connections(entries, self.session)
        entries[0]['source_outgoing'] = ['5']
        entries[0]['connection_notes'] = []
        result = await services.import_service_connections(entries, self.session)
        self.assertEqual(result['updated'], 1)
        connections = await self.connections()
        self.assertEqual(len(connections), 1)
        self.assertEqual(connections[0].source_outgoing, ['5'])
        self.assertEqual(connections[0].connection_notes, [])

    async def test_municipalities(self):
        for town in ('A', 'B'):
            await services.import_service_connections(services.normalize_row(row(municipality=town)), self.session)
        self.assertEqual(len(await self.objects()), 3)
        self.assertEqual(len(await self.connections()), 2)

    async def test_gps_matches_excel_and_asset_id(self):
        await services.import_service_connections(services.normalize_row(row()), self.session)
        payload = GPSImportData(points=[dict(name='Chäppelimatt 5', lon=8, lat=47, ckw_id='123', type='building', location='A')])
        await services.import_gps_objects(payload, self.session)
        await services.import_gps_objects(payload, self.session)
        payload.points[0].name = 'Neuer Name 5'
        await services.import_gps_objects(payload, self.session)
        objects = await self.objects()
        self.assertEqual(len(objects), 2)
        building = next(o for o in objects if o.ckw_id == '123')
        self.assertIsNotNone(building.geom)
        self.assertEqual(building.type, 'building')
        self.assertEqual(building.name, 'NeuerName5')

    async def test_duplicate_in_batch_last_notes_win(self):
        entry = services.normalize_row(row())[0]
        result = await services.import_service_connections([entry, {**entry, 'connection_notes': ['Last']}], self.session)
        self.assertEqual(result['created'], 1)
        self.assertTrue(result['warnings'])
        self.assertEqual((await self.connections())[0].connection_notes, ['Last'])

    async def test_multiple_connections_reimport_and_ambiguity(self):
        entry = services.normalize_row(row())[0]
        batch = [entry, {**entry, 'source_outgoing': ['5']}]
        await services.import_service_connections(batch, self.session)
        result = await services.import_service_connections(batch, self.session)
        self.assertEqual(result['created'], 0)
        self.assertEqual(len(await self.connections()), 2)
        result = await services.import_service_connections([{**entry, 'source_outgoing': ['6']}], self.session)
        self.assertEqual(result['skipped'], 1)
        self.assertTrue(result['warnings'])
        self.assertEqual(len(await self.connections()), 2)

    async def test_missing_references_reported(self):
        entry = services.normalize_row(row())[0]
        entry.update(source_name='Missing', unswitched_terminal='Missing DP', first_disconnect_point='Missing DB')
        result = await services.import_service_connections([entry], self.session)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual({w['field'] for w in result['warnings']}, {'transformer', 'disconnect_point', 'distribution_box'})
        self.assertEqual(await self.connections(), [])

    async def test_none_preserves_arrays(self):
        entry = services.normalize_row(row())[0]
        await services.import_service_connections([entry], self.session)
        await services.import_service_connections([{**entry, 'source_outgoing': None, 'connection_notes': None}], self.session)
        connection = (await self.connections())[0]
        self.assertEqual(connection.source_outgoing, ['4'])
        self.assertEqual(connection.connection_notes, ['PVA: 30 kVA'])

    async def test_null_items_and_normalized_arrays(self):
        entry = services.normalize_row(row())[0]
        entry['source_outgoing'] = [' 8 ', None, '9']
        await services.import_service_connections([entry], self.session)
        entry['source_outgoing'] = ['8', '9']
        result = await services.import_service_connections([entry], self.session)
        self.assertEqual(result['unchanged'], 1)
        self.assertEqual((await self.connections())[0].source_outgoing, ['8', '9'])

    async def test_legacy_blank_location_and_spaced_name(self):
        self.session.add(Object(name='Chäppelimatt 5', type='building', ckw_id='asset'))
        await self.session.commit()
        await services.import_service_connections(services.normalize_row(row()), self.session)
        building = next(o for o in await self.objects() if o.type == 'building')
        self.assertEqual((building.name, building.location, building.ckw_id), ('Chäppelimatt5', 'A', 'asset'))
        self.assertEqual(len(await self.objects()), 2)

    async def test_ambiguous_names_and_duplicate_assets(self):
        self.session.add_all([Object(name='Haus1', type='building', location=town, ckw_id='same') for town in ('A', 'B')])
        await self.session.commit()
        for point in [dict(name='Haus 1', lat=47, lon=8), dict(name='Renamed', ckw_id='same', lat=47, lon=8)]:
            result = await services.import_gps_objects(GPSImportData(points=[point]), self.session, return_report=True)
            self.assertEqual(result['skipped'], 1)
        self.assertEqual(len(await self.objects()), 3)

    async def test_conflicting_asset_id_not_overwritten(self):
        self.session.add(Object(name='Haus1', type='building', ckw_id='one'))
        await self.session.commit()
        result = await services.import_gps_objects(GPSImportData(points=[dict(name='Haus 1', ckw_id='two', lat=47, lon=8)]), self.session, return_report=True)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(len(await self.objects()), 2)

    async def test_gps_preserves_missing_attributes_and_friendly_name(self):
        await services.import_service_connections(services.normalize_row(row()), self.session)
        payload = GPSImportData(points=[dict(name='Chäppelimatt5', lon=8, lat=47, ckw_id='123')])
        await services.import_gps_objects(payload, self.session)
        payload.points[0].ckw_id = None
        await services.import_gps_objects(payload, self.session)
        building = next(o for o in await self.objects() if o.type == 'building')
        self.assertEqual((building.ckw_id, building.friendly_name, building.location), ('123', 'Chäppelimatt 5', 'A'))

    async def test_multiple_sources_warning_retains_names(self):
        data = list(row())
        data[7] = 'Transformer A\nTransformer B'
        result = await services.import_service_connections(services.normalize_row(data), self.session)
        warning = next(w for w in result['warnings'] if w['reason'] == 'multiple_sources_single_transformer')
        self.assertEqual(warning['values'], ['Transformer A', 'Transformer B'])
        self.assertEqual(result['created'], 1)

    async def test_positional_notes_persist(self):
        entries = services.normalize_row(row('Haus 1\nHaus 2\nHaus 3', '\n\nLadestation: 11 kVA'))
        await services.import_service_connections(entries, self.session)
        objects = {o.id: o.name for o in await self.objects()}
        self.assertEqual({objects[c.building_id]: c.connection_notes for c in await self.connections()},
                         {'Haus1': [], 'Haus2': [], 'Haus3': ['Ladestation: 11 kVA']})

    async def test_excel_route_reimport(self):
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = 'Modified'
        worksheet.append(['Gemeinde', 'Objekt', 'Assek', 'Bemerkung', 'TK', 'Trennstelle', 'Abgang', 'Speisung', 'Abgang'])
        worksheet.append(row('Haus 1\nHaus 2\nHaus 3', '\n\nLadestation: 11 kVA'))
        data = BytesIO()
        workbook.save(data)
        workbook.close()
        for _ in range(2):
            result = await import_excel(UploadFile(file=BytesIO(data.getvalue()), filename='test.xlsx'), self.session)
        self.assertEqual(result['created'], 0)
        self.assertEqual(result['unchanged'], 3)
        self.assertEqual(result['warnings'], [])
        self.assertEqual(len(await self.connections()), 3)

    async def test_optional_reference_update_preserve_and_clear(self):
        self.session.add_all([Object(name=name, type='distribution_box') for name in ('Box1', 'Box2')])
        await self.session.commit()
        entry = services.normalize_row(row())[0]
        entry['first_disconnect_point'] = 'Box 1'
        await services.import_service_connections([entry], self.session)
        original = (await self.connections())[0].distribution_box_id
        entry['first_disconnect_point'] = None
        await services.import_service_connections([entry], self.session)
        self.assertEqual((await self.connections())[0].distribution_box_id, original)
        entry['first_disconnect_point'] = 'Box 2'
        await services.import_service_connections([entry], self.session)
        self.assertNotEqual((await self.connections())[0].distribution_box_id, original)
        entry['first_disconnect_point'] = ''
        await services.import_service_connections([entry], self.session)
        self.assertIsNone((await self.connections())[0].distribution_box_id)
        self.assertEqual(len(await self.connections()), 1)

    async def test_legacy_idempotence(self):
        data = ImportData(raw_entries=services.normalize_row(row()))
        with patch.object(services, 'AsyncSessionLocal', self.sessions):
            await services.import_to_db(data)
            result = await services.import_to_db(data)
        self.assertEqual(result['created'], 0)
        self.assertEqual(len(await self.objects()), 2)
        self.assertEqual(len(await self.connections()), 1)

    async def test_unexpected_failure_rolls_back(self):
        original = self.session.flush
        calls = 0

        async def failing_flush(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('test failure')
            return await original(*args, **kwargs)

        with patch.object(self.session, 'flush', side_effect=failing_flush):
            with self.assertRaises(RuntimeError):
                await services.import_service_connections(services.normalize_row(row()), self.session)
        self.assertEqual(len(await self.objects()), 1)
        self.assertEqual(await self.connections(), [])


@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), 'Set TEST_POSTGRES_URL for PostGIS integration tests')
class PostgresImportTests(ImportTests):
    """The same regressions on real JSONB, geometry, and PostgreSQL transactions.

    Use a random isolated schema; never read/write the application's data tables.
    The database must already provide PostGIS and the object_type enum.
    """
    async def asyncSetUp(self):
        self.schema = 'import_test_' + uuid.uuid4().hex
        self.engine = create_async_engine(os.environ['TEST_POSTGRES_URL'],
                                          execution_options={'schema_translate_map': {None: self.schema}})
        async with self.engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            await conn.run_sync(lambda c: Object.__table__.create(c))
            await conn.run_sync(lambda c: ServiceConnection.__table__.create(c))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session = self.sessions()
        self.session.add(Object(name='TransformerA', friendly_name='Transformer A', type='transformer'))
        await self.session.commit()

    async def asyncTearDown(self):
        await self.session.close()
        async with self.engine.begin() as conn:
            # Only this test's generated schema can be dropped.
            assert self.schema.startswith('import_test_') and len(self.schema) == 44
            await conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.engine.dispose()

    async def test_concurrent_imports(self):
        async def run():
            async with self.sessions() as session:
                return await services.import_service_connections(services.normalize_row(row()), session)
        results = await asyncio.gather(run(), run())
        self.assertEqual(sum(r['created'] for r in results), 1)
        self.assertEqual(len(await self.objects()), 2)
        self.assertEqual(len(await self.connections()), 1)


if __name__ == '__main__':
    unittest.main()
