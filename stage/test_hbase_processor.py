# Copyright 2018 StreamSets Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import datetime
import json
import logging
import string

import pytest
from streamsets.sdk.utils import Version
from streamsets.testframework.markers import cluster
from streamsets.testframework.utils import get_random_string

logger = logging.getLogger(__name__)

# Specify a port for SDC RPC stages to use.
SDC_RPC_PORT = 20000


@pytest.fixture(autouse=True)
def version_check(sdc_builder, cluster):
    if cluster.version == 'cdh6.0.0' and Version('3.5.0') <= Version(sdc_builder.version) < Version('3.6.0'):
        pytest.skip('HBase Lookup processor is not included in streamsets-datacollector-cdh_6_0-lib in SDC 3.5')


@cluster('cdh', 'hdp')
def test_hbase_lookup_processor(sdc_builder, sdc_executor, cluster):
    """Simple HBase Lookup processor test.
    Pipeline will enrich records with the name of Grand Tours by adding a field containing the year
    of their first editions, which will come from an HBase table.
    dev_raw_data_source >> hbase_lookup >> trash
    """
    # Generate some silly data.
    bike_races = [dict(name='Tour de France', first_edition='1903'),
                  dict(name="Giro d'Italia", first_edition='1909'),
                  dict(name='Vuelta a Espana', first_edition='1935')]

    # Convert to raw data for the Dev Raw Data Source.
    raw_data = '\n'.join(bike_race['name'] for bike_race in bike_races)

    # Generate HBase Lookup's attributes.
    lookup_parameters = [dict(rowExpr="${record:value('/text')}",
                              columnExpr='info:first_edition',
                              outputFieldPath='/founded',
                              timestampExpr='')]

    # Get random table name to avoid collisions.
    table_name = get_random_string(string.ascii_letters, 10)

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='TEXT', raw_data=raw_data)

    # Create HBase Lookup processor.
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name)

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'info': {}})

        # Use HappyBase's `Batch` instance to avoid unnecessary calls to HBase.
        batch = cluster.hbase.client.table(table_name).batch()
        for bike_race in bike_races:
            # Use of str.encode() below is because HBase (and HappyBase) speaks in byte arrays.
            batch.put(bike_race['name'].encode(), {b'info:first_edition': bike_race['first_edition'].encode()})
        batch.send()

        # Take a pipeline snapshot.
        snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
        sdc_executor.stop_pipeline(pipeline)

        # Validate output.
        assert [dict(name=record.field['text'],
                     first_edition=record.field['founded'])
                for record in snapshot[hbase_lookup.instance_name].output] == bike_races

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


@cluster('cdh', 'hdp')
def test_hbase_lookup_processor_empty_batch(sdc_builder, sdc_executor, cluster):
    """HBase Lookup processor test.
    pipeline will receive an empty batch, not errors would be shown
    dev_raw_data_source >> hbase_lookup >> trash
    """
    # Create empty input data.
    raw_data = ""

    # Generate HBase Lookup's attributes.
    lookup_parameters = [dict(rowExpr="${record:value('/text')}",
                              columnExpr='info:empty',
                              outputFieldPath='/founded',
                              timestampExpr='')]

    # Get random table name to avoid collisions.
    table_name = get_random_string(string.ascii_letters, 10)

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='TEXT', raw_data=raw_data)

    # Create HBase Lookup processor.
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name)

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'info': {}})

        # Start pipeline.
        sdc_executor.start_pipeline(pipeline)
        sdc_executor.stop_pipeline(pipeline)

        assert 0 == len(list(cluster.hbase.client.table(table_name).scan()))

        status = sdc_executor.get_pipeline_status(pipeline).response.json().get('status')
        assert 'STOPPED' == status
    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


@cluster('cdh', 'hdp')
def test_hbase_lookup_processor_invalid_url(sdc_builder, sdc_executor, cluster):
    """HBase Lookup processor test.
    pipeline will have an invalid url, not errors would be shown
    dev_raw_data_source >> hbase_lookup >> trash
    """
    # Generate some silly data.
    bike_races = [dict(name='Tour de France', first_edition='1903'),
                  dict(name="Giro d'Italia", first_edition='1909'),
                  dict(name='Vuelta a Espana', first_edition='1935')]

    # Convert to raw data for the Dev Raw Data Source.
    raw_data = '\n'.join(bike_race['name'] for bike_race in bike_races)

    # Generate HBase Lookup's attributes.
    lookup_parameters = [dict(rowExpr="${record:value('/text')}",
                              columnExpr='info:empty',
                              outputFieldPath='/founded',
                              timestampExpr='')]

    # Get random table name to avoid collisions.
    table_name = get_random_string(string.ascii_letters, 10)

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='TEXT', raw_data=raw_data)

    # Create HBase Lookup processor.
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name)
    hbase_lookup.zookeeper_quorum = None

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'info': {}})

        # Use HappyBase's `Batch` instance to avoid unnecessary calls to HBase.
        batch = cluster.hbase.client.table(table_name).batch()
        for bike_race in bike_races:
            # Use of str.encode() below is because HBase (and HappyBase) speaks in byte arrays.
            batch.put(bike_race['name'].encode(), {b'info:first_edition': bike_race['first_edition'].encode()})
        batch.send()

        # Run preview.
        preview = sdc_executor.run_pipeline_preview(pipeline).preview
        assert preview is not None

        assert preview.issues.issues_count == 0

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


@cluster('cdh', 'hdp')
def test_hbase_lookup_processor_invalid_table_name(sdc_builder, sdc_executor, cluster):
    """HBase Lookup processor test.
    pipeline will have an invalid table name, not errors would be shown
    dev_raw_data_source >> hbase_lookup >> trash
    """
    # Generate some silly data.
    bike_races = [dict(name='Tour de France', first_edition='1903'),
                  dict(name="Giro d'Italia", first_edition='1909'),
                  dict(name='Vuelta a Espana', first_edition='1935')]

    # Convert to raw data for the Dev Raw Data Source.
    raw_data = '\n'.join(bike_race['name'] for bike_race in bike_races)

    # Generate HBase Lookup's attributes.
    lookup_parameters = [dict(rowExpr="${record:value('/text')}",
                              columnExpr='info:empty',
                              outputFieldPath='/founded',
                              timestampExpr='')]

    # Get invalid table name.
    table_name = 'randomTable'

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='TEXT', raw_data=raw_data)

    # Create HBase Lookup processor.
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name)
    hbase_lookup.zookeeper_quorum = None

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'info': {}})

        # Use HappyBase's `Batch` instance to avoid unnecessary calls to HBase.
        batch = cluster.hbase.client.table(table_name).batch()
        for bike_race in bike_races:
            # Use of str.encode() below is because HBase (and HappyBase) speaks in byte arrays.
            batch.put(bike_race['name'].encode(), {b'info:first_edition': bike_race['first_edition'].encode()})
        batch.send()

        # Run preview.
        preview = sdc_executor.run_pipeline_preview(pipeline).preview
        assert preview is not None

        assert preview.issues.issues_count == 0

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


@cluster('cdh', 'hdp')
def test_hbase_empty_key_expression(sdc_builder, sdc_executor, cluster):
    """Check empty key expression in hbase lookup processor gives a configuration issue
    dev_raw_data_source >> hbase_lookup >> trash
    """
    # Generate some silly data.
    bike_races = [dict(name='Tour de France', first_edition='1903'),
                  dict(name="Giro d'Italia", first_edition='1909'),
                  dict(name='Vuelta a Espana', first_edition='1935')]

    # Convert to raw data for the Dev Raw Data Source.
    raw_data = '\n'.join(bike_race['name'] for bike_race in bike_races)

    # Generate HBase Lookup's attributes.
    lookup_parameters = [dict(rowExpr='',
                              columnExpr='info:first_edition',
                              outputFieldPath='/founded',
                              timestampExpr='')]

    # Get random table name to avoid collisions.
    table_name = get_random_string(string.ascii_letters, 10)

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='TEXT', raw_data=raw_data)

    # Create HBase Lookup processor.
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name)

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'info': {}})

        issues = sdc_executor.api_client.export_pipeline(pipeline.id)['pipelineConfig']['issues']
        assert 0 == issues['issueCount']

        # Start pipeline.
        with pytest.raises(Exception) as e:
            sdc_executor.start_pipeline(pipeline)
            sdc_executor.stop_pipeline(pipeline)
        assert 'HBASE_35' in e.value.message
        assert 'HBASE_35 - Row key field has empty value' in e.value.message

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


@cluster('cdh', 'hdp')
def test_hbase_get_empty_key_to_discard(sdc_builder, sdc_executor, cluster):
    """Check no error record when there is no key in the record and ignore row missing field is set to true
    dev_raw_data_source >> hbase_lookup >> trash
    """

    data = {'row_key': 11, 'columnField': 'cf1:column'}
    json_data = json.dumps(data)

    # Generate HBase Lookup's attributes.
    lookup_parameters = [dict(rowExpr="${record:value('/row_key')}",
                              columnExpr="${record:value('/columnField')}",
                              outputFieldPath='/output',
                              timestampExpr='')]

    # Get random table name to avoid collisions.
    table_name = get_random_string(string.ascii_letters, 10)

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.data_format = 'JSON'
    dev_raw_data_source.raw_data = json_data

    # Create HBase Lookup processor.
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name, on_record_error='TO_ERROR')

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'cf1': {}})

        # Take a pipeline snapshot.
        snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
        sdc_executor.stop_pipeline(pipeline)

        scan = cluster.hbase.client.table(table_name).scan()

        assert 0 == len(list(scan))

        stage = snapshot[hbase_lookup.instance_name]
        logger.info('Error records %s ...', stage.error_records)

        assert len(stage.error_records) == 0

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


@cluster('cdh', 'hdp')
def test_hbase_get_empty_key_to_error(sdc_builder, sdc_executor, cluster):
    """Check record is sent to error when there is no key in the record and ignore row missing field is set to false
    dev_raw_data_source >> hbase_lookup >> trash
    """

    data = {'columnField': 'cf1:column'}
    json_data = json.dumps(data)

    # Generate HBase Lookup's attributes.
    lookup_parameters = [dict(rowExpr="${record:value('/row_key')}",
                              columnExpr="${record:value('/columnField')}",
                              outputFieldPath='/output',
                              timestampExpr='')]

    # Get random table name to avoid collisions.
    table_name = get_random_string(string.ascii_letters, 10)

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.data_format = 'JSON'
    dev_raw_data_source.raw_data = json_data

    # Create HBase Lookup processor.
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name, on_record_error='TO_ERROR',
                                ignore_row_missing_field=False)

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'cf1': {}})

        # Take a pipeline snapshot.
        snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
        sdc_executor.stop_pipeline(pipeline)

        scan = cluster.hbase.client.table(table_name).scan()

        assert 0 == len(list(scan))

        stage = snapshot[hbase_lookup.instance_name]
        logger.info('Error records %s ...', stage.error_records)

        assert len(stage.error_records) == 1

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


@cluster('cdh', 'hdp')
def test_hbase_lookup_processor_invalid_column_family(sdc_builder, sdc_executor, cluster):
    """HBase Lookup processor test.
    pipeline will have an invalid column family, HBase_37 error expected ()
    dev_raw_data_source >> hbase_lookup >> trash
    """
    # Generate some silly data.
    bike_races = [dict(name='Tour de France', first_edition='1903'),
                  dict(name="Giro d'Italia", first_edition='1909'),
                  dict(name='Vuelta a Espana', first_edition='1935')]

    # Convert to raw data for the Dev Raw Data Source.
    raw_data = '\n'.join(bike_race['name'] for bike_race in bike_races)

    # Generate HBase Lookup's attributes.
    lookup_parameters = [
        dict(rowExpr="${record:value('/text')}", columnExpr='info:first_edition', outputFieldPath='/founded',
             timestampExpr=''),
        dict(rowExpr="${record:value('/text')}", columnExpr='invalid:column', outputFieldPath='/founded',
             timestampExpr='')]

    # Get random table name to avoid collisions.
    table_name = get_random_string(string.ascii_letters, 10)

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='TEXT', raw_data=raw_data, )

    # Create HBase Lookup processor.
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(on_record_error='TO_ERROR', lookup_parameters=lookup_parameters, table_name=table_name)

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'info': {}})

        # Start pipeline.
        with pytest.raises(Exception) as e:
            sdc_executor.start_pipeline(pipeline)
            sdc_executor.stop_pipeline(pipeline)
        assert 'HBASE_36' in e.value.message

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


@cluster('cdh', 'hdp')
def test_hbase_lookup_processor_get_row(sdc_builder, sdc_executor, cluster):
    """Test a HBase processor using row id and column as lookup keys.

    It is expected the 'info:first_edition' be added as a new '/founded' record field.

    Pipeline:
        dev_raw_data_source >> hbase_lookup >> trash

    """
    # Generate some silly data.
    bike_races = [dict(name='Tour de France', first_edition='1903'),
                  dict(name='Giro d Italia', first_edition='1909'),
                  dict(name='Vuelta a Espana', first_edition='1935')]

    expected = [(b'Giro d Italia', {b'info:first_edition': b'1909'}),
                (b'Tour de France', {b'info:first_edition': b'1903'}),
                (b'Vuelta a Espana', {b'info:first_edition': b'1935'})]

    # Convert to raw data for the Dev Raw Data Source.
    raw_data = '\n'.join(bike_race['name'] for bike_race in bike_races)

    # Generate HBase Lookup's attributes.
    lookup_parameters = [dict(rowExpr="${record:value('/text')}",
                              columnExpr='info:first_edition',
                              outputFieldPath='/founded',
                              timestampExpr='')]

    # Get random table name to avoid collisions.
    table_name = get_random_string(string.ascii_letters, 10)

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='TEXT', raw_data=raw_data)

    # Create HBase Lookup processor.
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name)

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'info': {}})

        # Use HappyBase's `Batch` instance to avoid unnecessary calls to HBase.
        batch = cluster.hbase.client.table(table_name).batch()
        for bike_race in bike_races:
            # Use of str.encode() below is because HBase (and HappyBase) speaks in byte arrays.
            batch.put(bike_race['name'].encode(), {b'info:first_edition': bike_race['first_edition'].encode()})
        batch.send()

        # Take a pipeline snapshot.
        snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
        sdc_executor.stop_pipeline(pipeline)

        # Validate output.
        assert [dict(name=record.field['text'],
                     first_edition=record.field['founded'])
                for record in snapshot[hbase_lookup.instance_name].output] == bike_races

        # Validate output.
        result_list = list(cluster.hbase.client.table(table_name).scan())
        assert result_list == expected

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


@cluster('cdh', 'hdp')
def test_hbase_lookup_processor_row_key_lookup(sdc_builder, sdc_executor, cluster):
    """Test a HBase processor using only a row id as lookup key.

    For a single row key, we expect all column families (in this case 2) to be returned.

    Pipeline:
        dev_raw_data_source >> hbase_lookup >> trash

    """
    # Generate some silly data.
    bike_races = [{'name': 'Tour de France', 'location:country': 'France', 'date:month': 'July'},
                  {'name': "Giro d'Italia", 'location:country': 'Italy', 'date:month': 'May-June'},
                  {'name': 'Vuelta a Espana', 'location:country': 'Spain', 'date:month': 'Aug-Sept'}]

    # Convert to raw data for the Dev Raw Data Source.
    raw_data = '\n'.join(bike_race['name'] for bike_race in bike_races)

    # Generate HBase Lookup's attributes.
    lookup_parameters = [dict(rowExpr="${record:value('/text')}",
                              outputFieldPath='/data',
                              timestampExpr='')]

    # Get random table name to avoid collisions.
    table_name = get_random_string(string.ascii_letters, 10)

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='TEXT', raw_data=raw_data)

    # Create HBase Lookup processor.
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name)

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'location': {}, 'date': {}})

        # Use HappyBase's `Batch` instance to avoid unnecessary calls to HBase.
        batch = cluster.hbase.client.table(table_name).batch()
        for bike_race in bike_races:
            # Use of str.encode() below is because HBase (and HappyBase) speaks in byte arrays.
            batch.put(bike_race['name'].encode(), {b'location:country': bike_race['location:country'].encode(),
                                                   b'date:month': bike_race['date:month'].encode()})
        batch.send()

        # Take a pipeline snapshot.
        snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
        sdc_executor.stop_pipeline(pipeline)

        # Validate output.
        assert [dict(dict(name=record.field['text']),
                     **record.field['data'])
                for record in snapshot[hbase_lookup.instance_name].output] == bike_races

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


# Test SDC-10865
@cluster('cdh', 'hdp')
def test_hbase_lookup_processor_row_timestamp_keys_lookup(sdc_builder, sdc_executor, cluster):
    """Test a HBase processor using row id and timestamp as lookup keys.

    We insert into HBase 3 different value versions for each column of the same row. Then we retrieve each of these
    versions employing the timestamp key.

    Pipeline:
        dev_raw_data_source >> field_type_converter >> hbase_lookup >> trash

    """
    # Generate some silly data.
    data = [{'name': 'John', 'location:country': 'UK', 'location:city': 'London',
             'timestamp': '1978-01-05 19:00:00'},
            {'name': 'John', 'location:country': 'USA', 'location:city': 'Detroit',
             'timestamp': '1980-06-05 20:00:00'},
            {'name': 'John', 'location:country': 'Australia', 'location:city': 'Sidney',
             'timestamp': '1982-12-05 19:00:00'}]

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    raw_data = '\n'.join(json.dumps(rec) for rec in data)
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='JSON', raw_data=raw_data)

    # Create Field Type Converter
    conversions = [{'fields': ['/timestamp'],
                    'targetType': 'DATETIME',
                    'dateFormat': 'YYYY_MM_DD_HH_MM_SS'}]
    field_type_converter = pipeline_builder.add_stage('Field Type Converter')
    field_type_converter.set_attributes(conversion_method='BY_FIELD',
                                        field_type_converter_configs=conversions)

    # Create HBase Lookup processor.
    table_name = get_random_string(string.ascii_letters, 10)
    lookup_parameters = [dict(rowExpr="${record:value('/name')}",
                              outputFieldPath='/hbase',
                              timestampExpr="${record:value('/timestamp')}")]
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name)

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> field_type_converter >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'location': {}})

        # Use HappyBase's `Table` instance to insert different timestamp per record.
        table = cluster.hbase.client.table(table_name)
        for record in data:
            # Python timestamp is expressed in seconds as a float, while HBase use milliseconds
            # from epoch as a integer
            ts = int(datetime.datetime.strptime(record['timestamp'], '%Y-%m-%d %H:%M:%S').timestamp() * 1000)
            # Use of str.encode() below is because HBase (and HappyBase) speaks in byte arrays.
            table.put(record['name'].encode(),
                      {b'location:country': record['location:country'].encode(),
                       b'location:city': record['location:city'].encode()},
                      timestamp=ts)

        # Take a pipeline snapshot.
        snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
        sdc_executor.stop_pipeline(pipeline)

        # Validate output.
        for rec in snapshot[hbase_lookup.instance_name].output:
            assert rec.field['location:country'] == rec.field['hbase']['location:country']
            assert rec.field['location:city'] == rec.field['hbase']['location:city']

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)


# Test SDC-10865
@cluster('cdh', 'hdp')
def test_hbase_lookup_processor_row_column_timestamp_keys_lookup(sdc_builder, sdc_executor, cluster):
    """Test a HBase processor using row id, column name and timestamp as lookup keys.

    We insert into a HBase table 3 different value versions for each column of a row. Then we retrieve each of these
    versions for a given column employing the column and timestamp keys.

    Pipeline:
        dev_raw_data_source >> field_type_converter >> hbase_lookup >> trash

    """
    # Generate some silly data.
    data = [{'name': 'John', 'location:country': 'UK', 'location:city': 'London',
             'timestamp': '1978-01-05 19:00:00'},
            {'name': 'John', 'location:country': 'USA', 'location:city': 'Detroit',
             'timestamp': '1980-06-05 20:00:00'},
            {'name': 'John', 'location:country': 'Australia', 'location:city': 'Sidney',
             'timestamp': '1982-12-05 19:00:00'}]

    pipeline_builder = sdc_builder.get_pipeline_builder()

    # Create Dev Raw Data Source stage.
    raw_data = '\n'.join(json.dumps(rec) for rec in data)
    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='JSON', raw_data=raw_data)

    # Create Field Type Converter
    conversions = [{'fields': ['/timestamp'],
                    'targetType': 'DATETIME',
                    'dateFormat': 'YYYY_MM_DD_HH_MM_SS'}]
    field_type_converter = pipeline_builder.add_stage('Field Type Converter')
    field_type_converter.set_attributes(conversion_method='BY_FIELD',
                                        field_type_converter_configs=conversions)

    # Create HBase Lookup processor.
    table_name = get_random_string(string.ascii_letters, 10)
    lookup_parameters = [dict(rowExpr="${record:value('/name')}",
                              columnExpr="location:country",
                              timestampExpr="${record:value('/timestamp')}",
                              outputFieldPath='/hbase')]
    hbase_lookup = pipeline_builder.add_stage('HBase Lookup')
    hbase_lookup.set_attributes(lookup_parameters=lookup_parameters, table_name=table_name)

    # Create trash destination.
    trash = pipeline_builder.add_stage('Trash')

    # Build pipeline.
    dev_raw_data_source >> field_type_converter >> hbase_lookup >> trash
    pipeline = pipeline_builder.build().configure_for_environment(cluster)
    pipeline.configuration['shouldRetry'] = False
    sdc_executor.add_pipeline(pipeline)

    try:
        logger.info('Creating HBase table %s ...', table_name)
        cluster.hbase.client.create_table(name=table_name, families={'location': {}})

        # Use HappyBase's `Table` instance to insert different timestamp per record.
        table = cluster.hbase.client.table(table_name)
        for record in data:
            # Python timestamp is expressed in seconds as a float, while HBase use milliseconds
            # from epoch as a integer
            ts = int(datetime.datetime.strptime(record['timestamp'], '%Y-%m-%d %H:%M:%S').timestamp() * 1000)
            # Use of str.encode() below is because HBase (and HappyBase) speaks in byte arrays.
            table.put(record['name'].encode(),
                      {b'location:country': record['location:country'].encode(),
                       b'location:city': record['location:city'].encode()},
                      timestamp=ts)

        # Take a pipeline snapshot.
        snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
        sdc_executor.stop_pipeline(pipeline)

        # Validate output.
        for rec in snapshot[hbase_lookup.instance_name].output:
            assert rec.field['location:country'] == rec.field['hbase']

    finally:
        # Delete HBase table.
        logger.info('Deleting HBase table %s ...', table_name)
        cluster.hbase.client.delete_table(name=table_name, disable=True)
