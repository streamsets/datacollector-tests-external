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

import logging

import pytest

logger = logging.getLogger(__name__)


@pytest.fixture(scope='module')
def sdc_common_hook():
    def hook(data_collector):
        data_collector.add_user('arvind', roles=['admin'])
        data_collector.add_user('girish', roles=['admin'])

    return hook


def test_pipeline_el_user(random_expression_pipeline_builder, sdc_executor):
    random_expression_pipeline_builder.expression_evaluator.header_attribute_expressions = [
        {'attributeToSet': 'user',
        'headerAttributeExpression': '${pipeline:user()}'}
    ]
    pipeline = random_expression_pipeline_builder.pipeline_builder.build()
    sdc_executor.add_pipeline(pipeline)

    # Run the pipeline as one user.
    sdc_executor.set_user('arvind')
    snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
    sdc_executor.stop_pipeline(pipeline)

    record = snapshot[random_expression_pipeline_builder.expression_evaluator.instance_name].output[0]
    assert record.header['values']['user'] == 'arvind'

    # And then try different user.
    sdc_executor.set_user('girish')
    snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
    sdc_executor.stop_pipeline(pipeline)

    record = snapshot[random_expression_pipeline_builder.expression_evaluator.instance_name].output[0]
    assert record.header['values']['user'] == 'girish'


def test_pipeline_el_name_title_id_version(random_expression_pipeline_builder, sdc_executor):
    random_expression_pipeline_builder.expression_evaluator.header_attribute_expressions = [
        {'attributeToSet': 'title', 'headerAttributeExpression': '${pipeline:title()}'},
        {'attributeToSet': 'name', 'headerAttributeExpression': '${pipeline:name()}'},
        {'attributeToSet': 'version', 'headerAttributeExpression': '${pipeline:version()}'},
        {'attributeToSet': 'id', 'headerAttributeExpression': '${pipeline:id()}'},
    ]
    pipeline = random_expression_pipeline_builder.pipeline_builder.build(title='Most Pythonic Pipeline')
    pipeline.metadata['dpm.pipeline.version'] = 42

    sdc_executor.add_pipeline(pipeline)
    snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
    sdc_executor.stop_pipeline(pipeline)

    record = snapshot[random_expression_pipeline_builder.expression_evaluator.instance_name].output[0]
    assert record.header['values']['name'] == pipeline.id
    assert record.header['values']['id'] == pipeline.id
    assert record.header['values']['title'] == pipeline.title
    assert record.header['values']['version'] == '42'


def test_str_unescape_and_replace_el(sdc_builder, sdc_executor):
    pipeline_builder = sdc_builder.get_pipeline_builder()

    dev_raw_data_source = pipeline_builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.data_format = 'TEXT'
    dev_raw_data_source.raw_data = 'here\nis\tsome\ndata'
    dev_raw_data_source.use_custom_delimiter = True
    dev_raw_data_source.custom_delimiter = '^^^'

    expression_evaluator = pipeline_builder.add_stage('Expression Evaluator')
    expression_evaluator.field_expressions = [
        {'fieldToSet': '/transformed',
         'expression': '${str:replace(record:value("/text"), str:unescapeJava("\\\\n"), "<NEWLINE>")}'},
        {'fieldToSet': '/transformed2',
         'expression': '${str:replace(record:value("/transformed"), str:unescapeJava("\\\\t"), "<TAB>")}'}
    ]

    trash = pipeline_builder.add_stage('Trash')

    dev_raw_data_source >> expression_evaluator >> trash
    pipeline = pipeline_builder.build()

    sdc_executor.add_pipeline(pipeline)
    snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
    input_records = snapshot[dev_raw_data_source.instance_name].output
    output_records = snapshot[expression_evaluator.instance_name].output
    assert len(output_records) == len(input_records)
    assert input_records[0].field['text'] == 'here\nis\tsome\ndata'
    assert output_records[0].field['text'] == 'here\nis\tsome\ndata'
    assert output_records[0].field['transformed'] == 'here<NEWLINE>is\tsome<NEWLINE>data'
    assert output_records[0].field['transformed2'] == 'here<NEWLINE>is<TAB>some<NEWLINE>data'


def test_record_el(random_expression_pipeline_builder, sdc_executor):
    random_expression_pipeline_builder.expression_evaluator.header_attribute_expressions = [
        {'attributeToSet': 'valueOrDefault', 'headerAttributeExpression': '${record:valueOrDefault("/non-existing", 3)}'},
    ]
    pipeline = random_expression_pipeline_builder.pipeline_builder.build(title='Record ELs')

    sdc_executor.add_pipeline(pipeline)
    snapshot = sdc_executor.capture_snapshot(pipeline, start_pipeline=True).snapshot
    sdc_executor.stop_pipeline(pipeline)

    record = snapshot[random_expression_pipeline_builder.expression_evaluator.instance_name].output[0]
    assert record.header['values']['valueOrDefault'] == '3'
