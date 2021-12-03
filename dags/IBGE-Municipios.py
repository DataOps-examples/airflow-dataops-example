from airflow import DAG
from airflow.utils.dates import days_ago
from airflow.models.connection import Connection
from airflow.operators.dummy import DummyOperator
from airflow.operators.python import PythonOperator

from requests import get
from warnings import filterwarnings
from xml.etree import ElementTree

from elasticsearch import Elasticsearch

retries     = 50
retry_delay = 5

def getGEOLocations(**kwargs):
    filterwarnings("ignore")

    conn = Connection.get_connection_from_secrets(conn_id = kwargs['conn_id'])
    url = conn.host

    response = get(url, stream=True)
    response.raw.decode_content = True
    events = ElementTree.iterparse(response.raw)
    
    locations = {}
    obj = {}
    for event, elem in events:
        if 'SimpleData' not in elem.tag:
            continue
        obj[elem.attrib['name']] = elem.text
        if elem.attrib['name'] == 'ALT':
            locations[obj['CD_GEOCODMU']] = dict(obj)
            obj = {}

    return locations

def getLocations(**kwargs):
    filterwarnings("ignore")

    conn = Connection.get_connection_from_secrets(conn_id = kwargs['conn_id'])
    url = conn.host

    response = get(url)
    locations = response.json()

    return locations

def sendLocations(**kwargs):
    return True

def sendGEOLocations(**kwargs):
    return True

def mergeGEOLocations(**kwargs):
    filterwarnings("ignore")

    locations = kwargs['ti'].xcom_pull(task_ids='get-Location')
    geoLocations = kwargs['ti'].xcom_pull(task_ids='get-GEOLocation')

    data = []

    for l in locations:
        new = dict(l)
        try:
            new['geo'] = geoLocations[new['id']]
            new['geoPos'] = "{},{}".format(new['geo']['LAT'], new['geo']['LONG'])
        except KeyError:
            pass
        data.append(new)

    return data

def indexDocs(**kwargs):
    filterwarnings("ignore")

    docs = kwargs['ti'].xcom_pull(task_ids='Merge-data')

    conn = Connection.get_connection_from_secrets(conn_id = kwargs['conn_id'])
    url = conn.host

    es = Elasticsearch(url)

    ret = []
    for d in docs:
        resp = es.index(index="ibgemunicipios", id=d['id'], document=d)
        ret.append(resp)

    return ret

dag = DAG('IBGE-Municipios', 
          start_date=days_ago(2), 
          schedule_interval='@daily',
          max_active_runs=1,
          catchup=False)

start_op = DummyOperator(task_id='start', dag=dag)
end_op = DummyOperator(task_id='end', dag=dag)

get_GEOLoc_op = PythonOperator(
    dag             = dag,
    task_id         = 'get-GEOLocation',
    retries         = retries,
    retry_delay     = retry_delay,
    python_callable = getGEOLocations,
    op_kwargs       = {'conn_id': 'GEOLocation-data'}
)

get_Loc_op = PythonOperator(
    dag             = dag,
    task_id         = 'get-Location',
    retries         = retries,
    retry_delay     = retry_delay,
    python_callable = getLocations,
    op_kwargs       = {'conn_id': 'Location-data'}
)

merge_op = PythonOperator(
    dag             = dag,
    task_id         = 'Merge-data',
    retries         = retries,
    retry_delay     = retry_delay,
    python_callable = mergeGEOLocations
)

index_op = PythonOperator(
    dag             = dag,
    task_id         = 'Index-ElasticSearch',
    retries         = retries,
    retry_delay     = retry_delay,
    python_callable = indexDocs,
    op_kwargs       = {'conn_id': 'Elasticsearch'}
)

start_op >> [ get_GEOLoc_op, get_Loc_op ] >> merge_op >> index_op >> end_op
