# Copyright 2017 StreamSets Inc.
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

import json
import logging
import string

import pytest
import sqlalchemy
from streamsets.testframework.markers import database
from streamsets.testframework.utils import get_random_string

logger = logging.getLogger(__name__)

@database('sqlserver')
def test_sql_server_change_tracking_no_more_data(sdc_builder, sdc_executor, database):
    """Validate end-to-end case with stopping pipeline after it read all the data from database. The pipeline
    will look like:
        sqlserver_change_tracking >> trash
    """
    if not database.is_ct_enabled:
        pytest.skip('Test only runs against SQL Server with CT enabled.')

    table_name = get_random_string(string.ascii_lowercase, 20)

    table = sqlalchemy.Table(table_name, sqlalchemy.MetaData(),
                             sqlalchemy.Column('id', sqlalchemy.Integer, primary_key=True),
                             sqlalchemy.Column('name', sqlalchemy.String(25)),
                             sqlalchemy.Column('dt', sqlalchemy.String(20)))

    rows_in_database = [{'id': 1, 'name': 'Ji Sun', 'dt': '2017-05-03'},
                        {'id': 2, 'name': 'Jarcec', 'dt': '2017-05-03'},
                        {'id': 3, 'name': 'Santhosh', 'dt': '2017-05-03'}]

    pipeline_builder = sdc_builder.get_pipeline_builder()
    sql_server_change_tracking = pipeline_builder.add_stage('SQL Server Change Tracking Client')
    sql_server_change_tracking.set_attributes(table_configs=[{'initialOffset': 0, 'schema': 'dbo', 'tablePattern': f'{table_name}'}])

    trash = pipeline_builder.add_stage('Trash')

    sql_server_change_tracking >> trash
    pipeline = pipeline_builder.build().configure_for_environment(database)
    sdc_executor.add_pipeline(pipeline)

    # insert sample data into the table
    try:
        logger.info('Creating table %s...', table_name)
        table.create(database.engine)

        connection = database.engine.connect()

        # enable change tracking on table
        connection.execute(f'ALTER TABLE {table_name} ENABLE change_tracking WITH (track_columns_updated = on)')

        # insert sample data
        logger.info('Adding %s rows into %s...', len(rows_in_database), table_name)
        connection.execute(table.insert(), rows_in_database)

        snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True, batch_size=10).snapshot
        sdc_executor.stop_pipeline(pipeline)

        # assert all the data captured have the same raw_data
        output_records = snapshot[sql_server_change_tracking.instance_name].output

        assert 3 == len(output_records)

        for i in range(0, 3):
            assert output_records[i].get_field_data('/id') == rows_in_database[i].get('id')
            assert output_records[i].get_field_data('/name') == rows_in_database[i].get('name')
            assert output_records[i].get_field_data('/dt') == rows_in_database[i].get('dt')
    finally:
        logger.info('Dropping table %s...', table_name)
        table.drop(database.engine)


@database('sqlserver')
@pytest.mark.parametrize('include_record', [True, False])
def test_sql_server_change_tracking_with_committed_offset(sdc_builder, sdc_executor, database, include_record):
    """Validate the pipeline with committed offset. Start the pipeline run the first batch, stop the pipeline and restart the pipeline
    with committed offset. The pipeline will look like:
        sqlserver_change_tracking >> trash
    """
    if not database.is_ct_enabled:
        pytest.skip('Test only runs against SQL Server with CT enabled.')

    table_name = get_random_string(string.ascii_lowercase, 20)

    table = sqlalchemy.Table(table_name, sqlalchemy.MetaData(),
                             sqlalchemy.Column('id', sqlalchemy.Integer, primary_key=True),
                             sqlalchemy.Column('name', sqlalchemy.String(25)),
                             sqlalchemy.Column('dt', sqlalchemy.String(20)))

    rows_in_database = [{'id': 1, 'name': 'Ji Sun', 'dt': '2017-05-03'},
                        {'id': 2, 'name': 'Jarcec', 'dt': '2017-05-03'},
                        {'id': 3, 'name': 'Santhosh', 'dt': '2017-05-03'}]

    table_configs = [{'initialOffset': 0, 'schema': 'dbo', 'tablePattern': f'{table_name}'}]
    pipeline_builder = sdc_builder.get_pipeline_builder()
    sql_server_change_tracking = pipeline_builder.add_stage('SQL Server Change Tracking Client')
    sql_server_change_tracking.set_attributes(include_the_latest_data_in_the_record=include_record,
                                              table_configs=table_configs)

    trash = pipeline_builder.add_stage('Trash')

    sql_server_change_tracking >> trash
    pipeline = pipeline_builder.build().configure_for_environment(database)
    sdc_executor.add_pipeline(pipeline)

    # insert sample data into the table
    try:
        logger.info('Creating table %s...', table_name)
        table.create(database.engine)

        connection = database.engine.connect()

        # enable change tracking on table
        connection.execute(f'ALTER TABLE {table_name} ENABLE change_tracking WITH (track_columns_updated = on)')

        # insert sample data
        logger.info('Adding %s rows into %s...', len(rows_in_database), table_name)
        connection.execute(table.insert(), rows_in_database)

        sdc_executor.start_pipeline(pipeline).wait_for_pipeline_batch_count(3)
        sdc_executor.stop_pipeline(pipeline)

        # delete sample data
        logger.info('Deleting %s rows into %s...', len(rows_in_database), table_name)
        connection.execute(table.delete(), rows_in_database)

        snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True, batch_size=10).snapshot
        sdc_executor.stop_pipeline(pipeline)

        # assert all the data captured have the same raw_data
        output_records = snapshot[sql_server_change_tracking.instance_name].output

        assert 3 == len(output_records)

        for i in range(0, 3):
            assert output_records[i].get_field_data('/id') == rows_in_database[i].get('id')
    finally:
        logger.info('Dropping table %s...', table_name)
        table.drop(database.engine)
