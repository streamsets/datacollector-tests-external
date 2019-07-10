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

import base64
import io
import json
import logging
import string
import random

import avro
import pytest
from avro.datafile import DataFileWriter
from streamsets.sdk.utils import Version
from streamsets.testframework.environments.cloudera import ClouderaManagerCluster
from streamsets.testframework.markers import cluster
from streamsets.testframework.utils import get_random_string

logger = logging.getLogger(__name__)

# Specify a port for SDC RPC stages to use.
SDC_RPC_PORT = 20000
SNAPSHOT_TIMEOUT_SEC = 150
MAX_BATCH_WAIT_TIME = 30

MIN_SDC_VERSION_WITH_SPARK_2_LIB = Version('3.3.0')

SCHEMA = {
    'namespace': 'example.avro',
    'type': 'record',
    'name': 'Employee',
    'fields': [
        {'name': 'name', 'type': 'string'},
        {'name': 'age', 'type': 'int'},
        {'name': 'emails', 'type': {'type': 'array', 'items': 'string'}},
        {'name': 'boss', 'type': ['Employee', 'null']}
    ]
}

@pytest.fixture(scope='function')
def port():
    return random.randrange(20000, 25000)

@pytest.fixture(autouse=True)
def kafka_check(cluster):
    if isinstance(cluster, ClouderaManagerCluster) and not hasattr(cluster, 'kafka'):
        pytest.skip('Kafka tests require Kafka to be installed on the cluster')


@cluster('cdh')
def test_kafka_origin_cluster(sdc_builder, sdc_executor, cluster, port):
    """Write simple text messages into Kafka and confirm that Kafka successfully reads them.
    Because cluster mode pipelines don't support snapshots, we do this verification using a
    second standalone pipeline whose origin is an SDC RPC written to by the Kafka Consumer pipeline.
    Specifically, this would look like:

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    message = 'Hello World from SDC & DPM!'
    expected = '{\'text\': Hello World from SDC & DPM!}'

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka String pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash

    snapshot_pipeline = builder.build(title='Cluster kafka String Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, message.encode(), 'TEXT')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected, 'TEXT')
    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_produce_string_records_multiple_partitions(sdc_builder, sdc_executor, cluster, port):
    """Write simple text messages into Kafka multiple partitions and confirm that Kafka successfully reads them.
    Because cluster mode pipelines don't support snapshots, we do this verification using a
    second standalone pipeline whose origin is an SDC RPC written to by the Kafka Consumer pipeline.
    Specifically, this would look like:

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    message = 'Hello World from SDC & DPM!'
    expected = '{\'text\': Hello World from SDC & DPM!}'

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster partitions pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, message.encode(), 'WITH_KEY')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected, 'TEXT')

    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_kafka_origin_multiple_json_objects_single_record_cluster(sdc_builder, sdc_executor, cluster, port):
    """Write json objects messages into Kafka and confirm that Kafka successfully reads them.
    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """
    message = {'Alex': 'Developer', 'Xavi': 'Developer'}
    expected = '{\'Alex\': Developer, \'Xavi\': Developer}'

    json_test(sdc_builder, sdc_executor, cluster, message, expected, port)


@cluster('cdh')
def test_kafka_origin_multiple_json_objects_multiple_records_cluster(sdc_builder, sdc_executor, cluster, port):
    """Write json objects messages into Kafka and confirm that Kafka successfully reads them.
    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    message = [{'Alex': 'Developer'}, {'Xavi': 'Developer'}]
    expected = '[{\'Alex\': Developer}, {\'Xavi\': Developer}]'

    json_test(sdc_builder, sdc_executor, cluster, message, expected, port)


@cluster('cdh')
def test_kafka_origin_json_array_cluster(sdc_builder, sdc_executor, cluster, port):
    """Write json array messages into Kafka and confirm that Kafka successfully reads them.
    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    message = ['Alex', 'Xavi']
    expected = '[Alex, Xavi]'

    json_test(sdc_builder, sdc_executor, cluster, message, expected, port)


@cluster('cdh')
def test_kafka_xml_record_cluster(sdc_builder, sdc_executor, cluster, port):
    """Write simple XML messages into Kafka and confirm that Kafka successfully reads them.

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    message = '<developers><developer>Alex</developer><developer>Xavi</developer></developers>'
    expected = '{\'developer\': [{\'value\': Alex}, {\'value\': Xavi}]}'

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)
    kafka_consumer.set_attributes(data_format='XML')

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka XML pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster kafka XML Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, message.encode(), 'XML')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected, 'XML')
    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_kafka_xml_record_delimiter_element_cluster(sdc_builder, sdc_executor, cluster, port):
    """Write simple XML messages into Kafka and confirm that Kafka successfully reads them.

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    message = '<developers><developer>Alex</developer><developer>Xavi</developer></developers>'
    expected = ['{\'value\': Alex}', '{\'value\': Xavi}']

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)
    kafka_consumer.set_attributes(data_format='XML', delimiter_element="developer")

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka XML pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster kafka XML Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, message.encode(), 'XML')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected,
                                    'XML_MULTI_ELEMENT')
    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_kafka_csv_record_cluster(sdc_builder, sdc_executor, cluster, port):
    """Write simple csv messages into Kafka and confirm that Kafka successfully reads them.

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    message = 'Alex,Xavi,Tucu,Martin'
    expected = 'OrderedDict([(\'0\', Alex), (\'1\', Xavi), (\'2\', Tucu), (\'3\', Martin)])'

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)
    kafka_consumer.set_attributes(data_format='DELIMITED')

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka CSV pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster kafka CSV Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, message.encode(), 'CSV')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected, 'CSV')
    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_kafka_binary_record_cluster(sdc_builder, sdc_executor, cluster, port):
    """Write simple binary messages into Kafka and confirm that Kafka successfully reads them.

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    message = 'Binary Text Example'
    expected = message.encode()

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)
    kafka_consumer.set_attributes(data_format='BINARY')

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka BINARY pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster kafka BINARY snapshot')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, message.encode(), 'BINARY')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected, 'BINARY')
    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_produce_avro_records_with_schema(sdc_builder, sdc_executor, cluster, port):
    """Write avro text messages into Kafka multiple partitions and confirm that Kafka successfully reads them.
    Because cluster mode pipelines don't support snapshots, we do this verification using a
    second standalone pipeline whose origin is an SDC RPC written to by the Kafka Consumer pipeline.
    Specifically, this would look like:

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    msg = {'name': 'boss', 'age': 60, 'emails': ['boss@company.com', 'boss2@company.com'], 'boss': None}
    expected = ('OrderedDict([(\'name\', boss), (\'age\', 60), (\'emails\', [boss@company.com, boss2@company.com]),'
                ' (\'boss\', None)])')

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)
    kafka_consumer.set_attributes(data_format='AVRO', avro_schema_location='INLINE', avro_schema=json.dumps(SCHEMA))

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka AVRO pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, msg, 'AVRO')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected, 'AVRO')
    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_produce_avro_records_without_schema(sdc_builder, sdc_executor, cluster, port):
    """Write avro text messages into Kafka multiple partitions with the schema in the records
    and confirm that Kafka successfully reads them.
    Because cluster mode pipelines don't support snapshots, we do this verification using a
    second standalone pipeline whose origin is an SDC RPC written to by the Kafka Consumer pipeline.
    Specifically, this would look like:

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    msg = {'name': 'boss', 'age': 60, 'emails': ['boss@company.com', 'boss2@company.com'], 'boss': None}
    expected = ('OrderedDict([(\'name\', boss), (\'age\', 60), (\'emails\', [boss@company.com, boss2@company.com]),'
                ' (\'boss\', None)])')

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)

    kafka_consumer.set_attributes(data_format='AVRO', avro_schema_location='SOURCE')

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka AVRO pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, msg, 'AVRO_WITHOUT_SCHEMA')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected,
                                    'AVRO_WITHOUT_SCHEMA')
    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_kafka_origin_syslog_message(sdc_builder, sdc_executor, cluster, port):
    """Write a text message using UDP datagram mode SYSLOG
    into Kafka multiple partitions with the schema in the records
    and confirm that Kafka successfully reads them.
    Because cluster mode pipelines don't support snapshots, we do this verification using a
    second standalone pipeline whose origin is an SDC RPC written to by the Kafka Consumer pipeline.
    Specifically, this would look like:

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """
    msg64packet = ("rO0ABXeOAAAAAQAAAAEAAAAAAAAAAQAJMTI3LjAuMC4xAAALuAAJMTI3LjAuMC4xAAAH0AAAAFw8MzQ+MSAyMDEz"
                   "LTA2LTI4VDA2OjE0OjU2LjAwMCswMjowMCBteW1hY2hpbmUgc3U6ICdzdSByb290JyBmYWlsZWQgZm9yIGxvbnZpY"
                   "2sgb24gL2Rldi9wdHMvOA==")

    expected = (
        '{\'severity\': 2, \'senderPort\': 3000, \'receiverAddr\': 127.0.0.1:2000, \'host\': mymachine, \'raw\': '
        '<34>1 2013-06-28T06:14:56.000+02:00 mymachine su: \'su root\' failed for lonvick on /dev/pts/8, '
        '\'senderAddr\': 127.0.0.1:3000, \'priority\': 34, \'facility\': 4, \'version\': 1, \'receiverPort\': 2000, '
        '\'remaining\': su: \'su root\' failed for lonvick on /dev/pts/8, \'timestamp\': 1372392896000}')

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)

    # Override default configuration.
    kafka_consumer.set_attributes(data_format='DATAGRAM', datagram_packet_format='SYSLOG')

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka SYSLOG pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, base64.b64decode(msg64packet), 'SYSLOG')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected, 'SYSLOG')

    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_kafka_origin_netflow_message(sdc_builder, sdc_executor, cluster, port):
    """Write a text message using UDP datagram mode NETFLOW
    into Kafka multiple partitions with the schema in the records
    and confirm that Kafka successfully reads them.
    Because cluster mode pipelines don't support snapshots, we do this verification using a
    second standalone pipeline whose origin is an SDC RPC written to by the Kafka Consumer pipeline.
    Specifically, this would look like:

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    msg64packet = ('rO0ABXoAAAIqAAAAAQAAAAIAAAAAAAAAAQAJMTI3LjAuMC4xAAALuAAJMTI3LjAuMC4xAAAH0AAAAfgABQAKAAAAAFVFcOIBWL'
                   'IwAAAAAAAAAAD3waSb49Wa8QAAAAAAAAAAAAAAAQAAAFlnyqItZ8qiLQA1JA8AABEAAAAAAAAAAAD3waSb49Wa8QAAAAAAAAAA'
                   'AAAAAQAAAFlnyqItZ8qiLQA1+ioAABEAAAAAAAAAAAD3waSb49Wa8QAAAAAAAAAAAAAAAQAAAFlnyqItZ8qiLQA1SWAAABEAAA'
                   'AAAAAAAAD55boV49Wa8QAAAAAAAAAAAAAAAQAAAFlnyqIvZ8qiLwA1q94AABEAAAAAAAAAAAB/472549Wa8QAAAAAAAAAAAAAA'
                   'AQAAAFlnyqIvZ8qiLwA1IlYAABEAAAAAAAAAAAB/472549Wa8QAAAAAAAAAAAAAAAQAAAFlnyqIvZ8qiLwA1l5sAABEAAAAAAA'
                   'AAAAB/472549Wa8QAAAAAAAAAAAAAAAQAAAFlnyqIvZ8qiLwA1u4EAABEAAAAAAAAAAAD55boV49Wa8QAAAAAAAAAAAAAAAQAA'
                   'AFlnyqIvZ8qiLwA14OQAABEAAAAAAAAAAAAtZyl349Wa8QAAAAAAAAAAAAAAAQAAArhnyqIxZ8qiMQA11FQAABEAAAAAAAAAAA'
                   'B5SzUv49Wa8QAAAAAAAAAAAAAAAQAAAfhnyqIyZ8qiMgA1FbUAABEAAAAAAAAAAAA=')

    expected = ['\'srcaddr\': -138304357', '\'first\': 1432355575064']

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)

    # Override default configuration.
    kafka_consumer.set_attributes(data_format='DATAGRAM', datagram_data_format='NETFLOW')

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka NETFLOW pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, base64.b64decode(msg64packet), 'NETFLOW')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected, 'NETFLOW')

    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_kafka_origin_collecd_message(sdc_builder, sdc_executor, cluster, port):
    """Write a text message using UDP datagram mode COLLECTD
    into Kafka multiple partitions with the schema in the records
    and confirm that Kafka successfully reads them.
    Because cluster mode pipelines don't support snapshots, we do this verification using a
    second standalone pipeline whose origin is an SDC RPC written to by the Kafka Consumer pipeline.
    Specifically, this would look like:

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    msg64packet = (
        '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')

    expected = (
        '{\'plugin_instance\': lo0, \'plugin\': interface, \'tx\': 0, \'rx\': 0, \'host\': ip-192-168-42-238.us-west-2.'
        'compute.internal, \'time_hires\': 1543518938371396391, \'type\': if_errors}')

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)

    # Override default configuration.
    kafka_consumer.set_attributes(data_format='DATAGRAM', datagram_data_format='COLLECTD')

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka COLLECTD pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, base64.b64decode(msg64packet), 'COLLECTD')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected, 'COLLECTD')

    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


@cluster('cdh')
def test_kafka_log_record_cluster(sdc_builder, sdc_executor, cluster, port):
    """Write simple log messages into Kafka and confirm that Kafka successfully reads them.

    Kafka Consumer Origin pipeline with cluster mode:
        kafka_consumer >> sdc_rpc_destination

    Snapshot pipeline:
        sdc_rpc_origin >> trash
    """

    message = ('+20150320 [15:53:31,161] DEBUG PipelineConfigurationValidator - Pipeline \'test:preview\' validation. '
               'valid=true, canPreview=true, issuesCount=0 - ')

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)

    # Override default configuration.
    kafka_consumer.set_attributes(data_format='LOG',
                                  log_format='LOG4J',
                                  retain_original_line=True,
                                  on_parse_error='INCLUDE_AS_STACK_TRACE')

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka BINARY pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster kafka BINARY snapshot')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, message.encode(), 'LOG')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, message, 'LOG')
    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


def get_kafka_consumer_stage(sdc_version, pipeline_builder, cluster):
    """Create and return a Kafka origin stage depending on execution mode for the pipeline."""
    pipeline_builder.add_error_stage('Discard')

    if Version(sdc_version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB:
        kafka_cluster_stage_lib = cluster.kafka.cluster_stage_lib_spark1
    else:
        kafka_cluster_stage_lib = cluster.kafka.cluster_stage_lib_spark2

    kafka_consumer = pipeline_builder.add_stage('Kafka Consumer',
                                                type='origin',
                                                library=kafka_cluster_stage_lib)
    kafka_consumer.set_attributes(data_format='TEXT',
                                  batch_wait_time_in_ms=20000,
                                  max_batch_size_in_records=10,
                                  rate_limit_per_partition_in_kafka_messages=10,
                                  topic=get_random_string(string.ascii_letters, 10),
                                  kafka_configuration=[{'key': 'auto.offset.reset', 'value': 'earliest'}])

    return kafka_consumer


def get_rpc_origin(builder, sdc_rpc_destination, port):
    """Create and return rpc origin stage with basic configuration"""
    sdc_rpc_origin = builder.add_stage(name='com_streamsets_pipeline_stage_origin_sdcipc_SdcIpcDSource')
    sdc_rpc_origin.sdc_rpc_listening_port = port
    sdc_rpc_origin.sdc_rpc_id = sdc_rpc_destination.sdc_rpc_id

    # Since YARN jobs take a while to get going, set RPC origin batch wait time to MAX_BATCH_WAIT_TIME (30s).
    sdc_rpc_origin.batch_wait_time_in_secs = MAX_BATCH_WAIT_TIME

    return sdc_rpc_origin


def get_rpc_destination(builder, sdc_executor, port):
    """Create and return rpc destination stage with basic configuration"""
    sdc_rpc_destination = builder.add_stage(name='com_streamsets_pipeline_stage_destination_sdcipc_SdcIpcDTarget')
    sdc_rpc_destination.sdc_rpc_connection.append('{}:{}'.format(sdc_executor.server_host, port))
    sdc_rpc_destination.sdc_rpc_id = get_random_string(string.ascii_letters, 10)

    return sdc_rpc_destination


def produce_kafka_messages(topic, cluster, message, data_format):
    """Send basic messages to Kafka"""
    producer = cluster.kafka.producer()

    basic_data_formats = ['XML', 'CSV', 'SYSLOG', 'NETFLOW', 'COLLECTD', 'BINARY', 'LOG', 'PROTOBUF', 'TEXT', 'JSON']

    # Write records into Kafka depending on the data_format.
    if data_format in basic_data_formats:
        producer.send(topic, message)

    elif data_format == 'WITH_KEY':
        producer.send(topic, message, key=get_random_string(string.ascii_letters, 10).encode())

    elif data_format == 'AVRO':
        writer = avro.io.DatumWriter(avro.schema.Parse(json.dumps(SCHEMA)))
        bytes_writer = io.BytesIO()
        encoder = avro.io.BinaryEncoder(bytes_writer)
        writer.write(message, encoder)
        raw_bytes = bytes_writer.getvalue()
        producer.send(topic, raw_bytes)

    elif data_format == 'AVRO_WITHOUT_SCHEMA':
        bytes_writer = io.BytesIO()
        datum_writer = avro.io.DatumWriter(avro.schema.Parse(json.dumps(SCHEMA)))
        data_file_writer = DataFileWriter(writer=bytes_writer, datum_writer=datum_writer,
                                          writer_schema=avro.schema.Parse(json.dumps(SCHEMA)))
        data_file_writer.append(message)
        data_file_writer.flush()
        raw_bytes = bytes_writer.getvalue()
        data_file_writer.close()
        producer.send(topic, raw_bytes)

    producer.flush()


def verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, message, data_format):
    """Start, stop pipeline and verify results using snapshot"""

    # Start Pipeline.
    snapshot_pipeline_command = sdc_executor.capture_snapshot(snapshot_pipeline, start_pipeline=True, wait=False)
    sdc_executor.start_pipeline(kafka_consumer_pipeline)

    logger.debug('Finish the snapshot and verify')
    snapshot_command = snapshot_pipeline_command.wait_for_finished(timeout_sec=SNAPSHOT_TIMEOUT_SEC)
    snapshot = snapshot_command.snapshot

    basic_data_formats = ['XML', 'CSV', 'SYSLOG', 'COLLECTD', 'PROTOBUF', 'TEXT', 'JSON', 'AVRO', 'AVRO_WITHOUT_SCHEMA']

    # Verify snapshot data.
    if data_format in basic_data_formats:
        record_field = [record.field for record in snapshot[snapshot_pipeline[0].instance_name].output]

        assert message == str(record_field[0])

    elif data_format == 'BINARY':
        record_field = [record.field for record in snapshot[snapshot_pipeline[0].instance_name].output]
        assert message == record_field[0]

    elif data_format == 'LOG':
        stage = snapshot[snapshot_pipeline[0].instance_name]
        assert 0 == len(stage.error_records)
        record_field = [record.field for record in snapshot[snapshot_pipeline[0].instance_name].output]
        assert message == str(record_field[0]['originalLine'])

    elif data_format == 'XML_MULTI_ELEMENT':
        record_field = [record.field for record in snapshot[snapshot_pipeline[0].instance_name].output]
        assert message[0] == str(record_field[0])
        assert message[1] == str(record_field[1])

    elif data_format == 'NETFLOW':
        record_field = [record.field for record in snapshot[snapshot_pipeline[0].instance_name].output]
        assert message[0] in str(record_field)
        assert message[1] in str(record_field)


def json_test(sdc_builder, sdc_executor, cluster, message, expected, port):
    """Generic method to tests using JSON format"""

    if (Version(sdc_builder.version) < MIN_SDC_VERSION_WITH_SPARK_2_LIB and
            ('kafka' in cluster.kerberized_services or cluster.kafka.is_ssl_enabled)):
        pytest.skip('Kafka cluster mode test only '
                    f'runs against cluster with the non-secured Kafka for SDC version {sdc_builder.version}.')

    # Build the Kafka consumer pipeline.
    builder = sdc_builder.get_pipeline_builder()
    kafka_consumer = get_kafka_consumer_stage(sdc_builder.version, builder, cluster)
    kafka_consumer.set_attributes(data_format='JSON')

    sdc_rpc_destination = get_rpc_destination(builder, sdc_executor, port)

    kafka_consumer >> sdc_rpc_destination
    kafka_consumer_pipeline = builder.build(title='Cluster kafka JSON pipeline').configure_for_environment(cluster)
    kafka_consumer_pipeline.configuration['executionMode'] = 'CLUSTER_YARN_STREAMING'
    kafka_consumer_pipeline.configuration['shouldRetry'] = False

    # Build the Snapshot pipeline.
    builder = sdc_builder.get_pipeline_builder()
    builder.add_error_stage('Discard')

    sdc_rpc_origin = get_rpc_origin(builder, sdc_rpc_destination, port)
    trash = builder.add_stage(label='Trash')
    sdc_rpc_origin >> trash
    snapshot_pipeline = builder.build(title='Cluster kafka JSON Snapshot pipeline')

    sdc_executor.add_pipeline(kafka_consumer_pipeline, snapshot_pipeline)

    try:
        # Publish messages to Kafka and verify using snapshot if the same messages are received.
        produce_kafka_messages(kafka_consumer.topic, cluster, json.dumps(message).encode(), 'JSON')
        verify_kafka_origin_results(kafka_consumer_pipeline, snapshot_pipeline, sdc_executor, expected, 'JSON')
    finally:
        sdc_executor.stop_pipeline(kafka_consumer_pipeline)
        sdc_executor.stop_pipeline(snapshot_pipeline)


