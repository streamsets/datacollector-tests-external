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

# Kafka Schema Registry Exchange tests.
#
# This test ensures that end-to-end behavior of pushing avro data through Kafka
# with exchanging schema in Confluent Schema Registry works as expected for all
# various combinations the schema can be configured.
#
# Currently the permutation contains the following axes:
# * Two different Kafka origins (Single Threaded, Multi Threaded)
# * Three different schema locations on generation side (header, inline, registry)

import logging
import string

import avro
import pytest
from streamsets.sdk.models import Configuration
from streamsets.testframework.markers import cluster, confluent, sdc_min_version
from streamsets.testframework.utils import get_random_string

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

AVRO_SCHEMA = '{"type":"record","name":"Brno","doc":"","fields":[{"name":"a","type":"int"},{"name":"b","type":"string"}]}'


@pytest.fixture(scope='function')
def topic():
    """Topic name used for this specific test."""
    topic = get_random_string(string.ascii_letters, 10)
    logger.debug('Using Topic: %s', topic)
    return topic


@pytest.fixture(scope='function')
def consumer_single(sdc_builder, topic, cluster, confluent):
    """Single threaded Kafka consumer configured to read schema from the registry."""
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    kafka_consumer = builder.add_stage('Kafka Consumer', library=cluster.kafka.standalone_stage_lib)
    kafka_consumer.set_attributes(topic=topic,
                                  data_format='AVRO',
                                  avro_schema_location='REGISTRY',
                                  lookup_schema_by='AUTO',
                                  key_deserializer='CONFLUENT',
                                  value_deserializer='CONFLUENT',
                                  kafka_configuration=[{'key': 'auto.offset.reset', 'value': 'earliest'}])

    trash = builder.add_stage(label='Trash')
    kafka_consumer >> trash

    return builder.build(title=f'Single Consumer for {topic}').configure_for_environment(cluster, confluent)


@pytest.fixture(scope='function')
def consumer_multi(sdc_builder, topic, cluster, confluent):
    """Multithreaded threaded Kafka consumer configured to read schema from the registry."""
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    kafka_consumer = builder.add_stage('Kafka Multitopic Consumer')
    kafka_consumer.set_attributes(topic_list=[topic],
                                  data_format='AVRO',
                                  avro_schema_location='REGISTRY',
                                  lookup_schema_by='AUTO',
                                  key_deserializer='CONFLUENT',
                                  value_deserializer='CONFLUENT',
                                  configuration_properties=[{'key': 'auto.offset.reset', 'value': 'earliest'}])

    trash = builder.add_stage(label='Trash')
    kafka_consumer >> trash

    return builder.build(title=f'Multi Consumer for {topic}').configure_for_environment(cluster, confluent)

@pytest.fixture(scope='function')
def producer_header(sdc_builder, topic, cluster, confluent):
    """Kafka producer that receives avro schema in record header."""
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    dev_raw_data_source = builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='JSON',
                                       raw_data='{"a": 1, "b": "Text"}',
                                       stop_after_first_batch=True)

    schema_generator = builder.add_stage('Schema Generator')
    schema_generator.schema_name = 'Brno'

    kafka_destination = builder.add_stage('Kafka Producer',
                                          library=cluster.kafka.standalone_stage_lib)

    kafka_destination.set_attributes(topic=topic,
                                     data_format='AVRO',
                                     avro_schema_location='HEADER',
                                     include_schema=False,
                                     register_schema=True,
                                     schema_subject=topic,
                                     key_serializer='CONFLUENT',
                                     value_serializer='CONFLUENT')

    dev_raw_data_source >> schema_generator >> kafka_destination
    return builder.build(title=f'Producer in Header for {topic}').configure_for_environment(cluster, confluent)


@pytest.fixture(scope='function')
def producer_inline(sdc_builder, topic, cluster, confluent):
    """Kafka producer that receives avro schema the pipeline configuration."""
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    dev_raw_data_source = builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='JSON',
                                       raw_data='{"a": 1, "b": "Text"}',
                                       stop_after_first_batch=True)

    kafka_destination = builder.add_stage('Kafka Producer',
                                          library=cluster.kafka.standalone_stage_lib)
    kafka_destination.set_attributes(topic=topic,
                                     data_format='AVRO',
                                     avro_schema_location='INLINE',
                                     avro_schema=AVRO_SCHEMA,
                                     include_schema=False,
                                     register_schema=True,
                                     schema_subject=topic,
                                     key_serializer='CONFLUENT',
                                     value_serializer='CONFLUENT')

    dev_raw_data_source >> kafka_destination
    return builder.build(title=f'Producer Inline for {topic}').configure_for_environment(cluster, confluent)


@pytest.fixture(scope='function')
def producer_registry(sdc_builder, topic, cluster, confluent):
    """Kafka producer that receives avro schema from schema registry (must exists before pipeline run)."""
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    dev_raw_data_source = builder.add_stage('Dev Raw Data Source')
    dev_raw_data_source.set_attributes(data_format='JSON',
                                       raw_data='{"a": 1, "b": "Text"}',
                                       stop_after_first_batch=True)

    kafka_destination = builder.add_stage('Kafka Producer',
                                          library=cluster.kafka.standalone_stage_lib)
    kafka_destination.set_attributes(topic=topic,
                                     data_format='AVRO',
                                     avro_schema_location='REGISTRY',
                                     include_schema=False,
                                     schema_subject=topic,
                                     key_serializer='CONFLUENT',
                                     value_serializer='CONFLUENT')

    dev_raw_data_source >> kafka_destination
    return builder.build(title=f'Producer Registry for {topic}').configure_for_environment(cluster, confluent)


@cluster('cdh', 'kafka')
@confluent
@sdc_min_version('3.1.0.0')
def test_single_header(sdc_executor, producer_header, consumer_single):
    perform_test(sdc_executor, producer_header, consumer_single)


@cluster('cdh', 'kafka')
@confluent
@sdc_min_version('3.1.0.0')
def test_multi_header(sdc_executor, producer_header, consumer_multi):
    perform_test(sdc_executor, producer_header, consumer_multi)


@cluster('cdh', 'kafka')
@confluent
@sdc_min_version('3.1.0.0')
def test_single_inline(sdc_executor, producer_inline, consumer_single):
    perform_test(sdc_executor, producer_inline, consumer_single)


@cluster('cdh', 'kafka')
@confluent
@sdc_min_version('3.1.0.0')
def test_multi_inline(sdc_executor, producer_inline, consumer_multi):
    perform_test(sdc_executor, producer_inline, consumer_multi)


@cluster('cdh', 'kafka')
@confluent
@sdc_min_version('3.1.0.0')
def test_single_registry(sdc_executor, topic, producer_registry, consumer_single, confluent):
    # We need to register the schema before running the pipelines
    schema = avro.schema.Parse(AVRO_SCHEMA)
    confluent.schema_registry.register(topic, schema)

    perform_test(sdc_executor, producer_registry, consumer_single)


@cluster('cdh', 'kafka')
@confluent
@sdc_min_version('3.1.0.0')
def test_multi_registry(sdc_executor, topic, producer_registry, consumer_multi, confluent):
    # We need to register the schema before running the pipelines
    schema = avro.schema.Parse(AVRO_SCHEMA)
    confluent.schema_registry.register(topic, schema)

    perform_test(sdc_executor, producer_registry, consumer_multi)


def perform_test(sdc_executor, producer, consumer):
    """Run the producer -> consumer pipeline and validate that we can properly read all the records."""
    # Add all pipelines
    sdc_executor.add_pipeline(producer, consumer)

    # Run them!
    sdc_executor.start_pipeline(producer).wait_for_finished()
    snapshot_command = sdc_executor.capture_snapshot(consumer, start_pipeline=True)
    sdc_executor.stop_pipeline(consumer)

    # Validate result
    snapshot = snapshot_command.snapshot
    assert snapshot is not None
    output = snapshot[consumer.origin_stage].output

    assert output is not None
    assert len(output) == 1
    assert output[0].field['a'].value == 1
    assert output[0].field['b'].value == 'Text'
