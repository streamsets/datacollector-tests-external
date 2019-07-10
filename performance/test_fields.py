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

"""
The tests in this module are for running high-volume pipelines, for the purpose of performance testing.
They will generate records from a raw source, run them through one or more processors, with a trash destination.
Output values will not be validated since the purpose of this test is to test performance and not correctness.
"""

import json
import logging
import uuid

import pytest

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


@pytest.mark.parametrize('number_of_records', (50_000, 100_000))
def test_field_path_stress_pipeline(sdc_builder, sdc_executor, benchmark, number_of_records):
    """
    Runs a pipeline with many field processor stages, which runs for a large number of records.
    """
    raw_data = """
    {
      "first": {
        "a": "one",
        "b": {
          "b1": 1.1,
          "b2": null,
          "b3": 1.3
        },
        "c": [3, 4, 5]
      },
      "second": {
        "d": "two",
        "e": {
          "b1": 2.1,
          "b2": null,
          "b3": 2.3
        },
        "f": [6, 7, 8]
      },
      "third": {
        "g": "three",
        "h": {
          "b1": 3.1,
          "b2": null,
          "b3": 3.3
        },
        "i": [9, 10, 11]
      }
    }
    """

    pipeline_builder = sdc_builder.get_pipeline_builder()

    source = pipeline_builder.add_stage('Dev Raw Data Source')
    source.set_attributes(data_format='JSON', raw_data=raw_data)

    remover = pipeline_builder.add_stage('Field Remover')
    remover.set_attributes(fields=['/second/f'], action='REMOVE')

    value_replacer = pipeline_builder.add_stage('Value Replacer', type='processor')
    value_replacer.set_attributes(replace_null_values=[{
        'fields': ['/*/*/*2'],
        'newValue': '42.0'
    }])

    type_converter_configs = [{
        'fields': ['/*/*/*2'],
        'targetType': 'DOUBLE',
        'dataLocale': 'en,US'
    }]
    type_converter = pipeline_builder.add_stage('Field Type Converter')
    type_converter.set_attributes(conversion_method='BY_FIELD',
                                  field_type_converter_configs=type_converter_configs)

    hasher_target_configs = [{
       'sourceFieldsToHash': ['/third/h*', '/third/g'],
       'hashType': 'MD5',
       'targetField': '/thirdHash'
    }]
    hasher = pipeline_builder.add_stage('Field Hasher')
    hasher.set_attributes(hash_to_target=hasher_target_configs,
                          hash_entire_record=False)

    masker_configs = [{
        'fields': ['/second/d'],
        'maskType': 'FIXED_LENGTH',
        'regex': '(.*)',
        'groupsToShow': '1'
    }]

    masker = pipeline_builder.add_stage('Field Masker')
    masker.set_attributes(field_mask_configs=masker_configs)

    trash = pipeline_builder.add_stage('Trash')

    source >> remover >> value_replacer >> type_converter >> hasher >> masker >> trash
    pipeline = pipeline_builder.build('Field Path Stress Test Pipeline - Many Stages')

    def benchmark_pipeline(executor, pipeline):
        pipeline.id = str(uuid.uuid4())
        executor.add_pipeline(pipeline)
        executor.start_pipeline(pipeline).wait_for_pipeline_output_records_count(number_of_records)
        executor.stop_pipeline(pipeline)
        executor.remove_pipeline(pipeline)

    benchmark.pedantic(benchmark_pipeline, args=(sdc_executor, pipeline), rounds=2)


@pytest.mark.parametrize('number_of_records', (50_000, 100_000))
def test_large_number_of_fields_stress_pipeline(sdc_builder, sdc_executor, benchmark, number_of_records):
    """
    Runs a pipeline with one processor that removes many fields from records that have a large number of fields.
    """
    raw_data_obj = {f'field{i}': i for i in range(1, 250)}
    raw_data = json.dumps(raw_data_obj)

    pipeline_builder = sdc_builder.get_pipeline_builder()

    source = pipeline_builder.add_stage('Dev Raw Data Source')
    source.set_attributes(data_format='JSON', raw_data=raw_data)

    remover = pipeline_builder.add_stage('Field Remover')
    remover.set_attributes(fields=['/field1*'], action='REMOVE')

    trash = pipeline_builder.add_stage('Trash')

    source >> remover >> trash
    pipeline = pipeline_builder.build('Field Path Stress Test Pipeline - Many Fields')

    def benchmark_pipeline(executor, pipeline):
        pipeline.id = str(uuid.uuid4())
        executor.add_pipeline(pipeline)
        executor.start_pipeline(pipeline).wait_for_pipeline_output_records_count(number_of_records)
        executor.stop_pipeline(pipeline)
        executor.remove_pipeline(pipeline)

    benchmark.pedantic(benchmark_pipeline, args=(sdc_executor, pipeline), rounds=2)
