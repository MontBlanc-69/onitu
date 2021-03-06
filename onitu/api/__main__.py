import sys

import zmq
import json
import pkg_resources

from logbook import Logger
from logbook.queues import ZeroMQHandler
from bottle import Bottle, run, response, redirect, static_file
from circus.client import CircusClient

from onitu.escalator.client import Escalator
from onitu.utils import get_fid, u, b, PY2, get_circusctl_endpoint
from onitu.utils import get_brocker_uri, get_logs_uri
from onitu.brocker.commands import GET_FILE
from onitu.brocker.responses import ERROR

if PY2:
    from urllib import unquote as unquote_
else:
    from urllib.parse import unquote as unquote_

ONITU_READTHEDOCS = "https://onitu.readthedocs.org/en/latest/api.html"

FACET_STATIC_ROOT = pkg_resources.resource_filename('onitu', 'facet/static/')

host = 'localhost'
port = 3862

app = Bottle()

session = u(sys.argv[1])
circus_client = CircusClient(endpoint=get_circusctl_endpoint(session))
escalator = Escalator(session)
logger = Logger("REST API")


@app.hook('after_request')
def enable_cors():
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = (
        'PUT, GET, POST, DELETE, OPTIONS'
    )
    response.headers['Access-Control-Allow-Headers'] = (
        'Origin, Accept, Content-Type, X-Requested-With, X-CSRF-Token'
    )


def unquote(string):
    return u(unquote_(string))


def service(name):
    driver = escalator.get(u'service:{}:driver'.format(name), default=None)
    if not driver:
        return None
    options = escalator.get(u'service:{}:options'.format(name))
    return {'name': name, 'driver': driver, 'options': options}


def service_exists(name):
    names = [service for service in escalator.get('services')]
    return name in names


def service_is_running(name):
    query = {
        "command": "status",
        "properties": {
            "name": name
        }
    }
    status = circus_client.call(query)
    if status['status'] == "active":
        return True
    else:
        return False


def error(error_code=500, error_message="internal server error"):
    response.status = error_code
    resp = {
        "status": "error",
        "reason": error_message
    }
    return resp


def file_not_found(name):
    return error(
        error_code=404,
        error_message="file {} not found".format(name)
    )


def file_not_found_in_folder(name, folder):
    return error(
        error_code=404,
        error_message="file {} not found in folder {}".format(name, folder)
    )


def service_not_found(name):
    return error(
        error_code=404,
        error_message=u"service {} not found".format(name)
    )


def service_not_running(name, already=False):
    fmt = u"service {} is {}stopped".format
    # "already" in case a stop has been requested on an already stopped service
    err_msg = fmt(name, "already ") if already else fmt(name, "")
    return error(
        error_code=409,
        error_message=err_msg
    )


def timeout():
    return error(
        error_code=408,
        error_message="timed out"
    )


def call_brocker(cmd, *args):
    context = zmq.Context.instance()
    dealer = context.socket(zmq.DEALER)
    dealer.connect(get_brocker_uri(session))

    try:
        message = [cmd] + [b(arg) for arg in args]
        dealer.send_multipart(message)
        return dealer.recv_multipart()
    finally:
        dealer.close()


@app.route('/')
def app_root():
    redirect('facet/index.html')


@app.route('/facet/<filename:path>')
def send_static(filename):
    return static_file(filename, root=FACET_STATIC_ROOT)


@app.route('/api', method='GET')
@app.route('/api/v1.0', method='GET')
def api_doc():
    redirect(ONITU_READTHEDOCS)


@app.route('/api/v1.0/files/id/<folder>/<name>', method='GET')
def get_file_id(folder, name):
    folder = u(unquote(folder))
    name = u(unquote(name))
    fid = get_fid(folder, name)
    metadata = escalator.get('file:{}'.format(fid), default=None)
    if metadata is None:
        return file_not_found_in_folder(name, folder)
    return {name: fid}


@app.route('/api/v1.0/files', method='GET')
def get_files():
    files = [metadata for key, metadata in escalator.range('file:')
             if key.count(':') == 1]
    for metadata in files:
        metadata['fid'] = get_fid(
            metadata['folder_name'], metadata['filename']
        )
    return {'files': files}


@app.route('/api/v1.0/files/<fid>/metadata', method='GET')
def get_file(fid):
    fid = unquote(fid)
    metadata = escalator.get('file:{}'.format(fid), default=None)
    if not metadata:
        return file_not_found(fid)
    metadata['fid'] = fid
    metadata['uptodate'] = [
        key.split(':')[-1]
        for key in escalator.range(u'file:{}:uptodate:'.format(fid),
                                   include_value=False)
    ]
    return metadata


@app.route('/api/v1.0/files/<fid>/content', method='GET')
def get_file_content(fid):
    fid = unquote(fid)
    metadata = escalator.get('file:{}'.format(fid), default=None)
    if not metadata:
        return file_not_found(fid)
    content = call_brocker(GET_FILE, fid)
    if content[0] != ERROR:
        response.content_type = metadata['mimetype']
        response.headers['Content-Disposition'] = (
            'attachment; filename="{}"'.format(b(metadata['filename']))
        )
        return content[1]
    return error()


@app.route('/api/v1.0/services', method='GET')
def get_services():
    services = [service(name) for name in escalator.get('services')]
    return {'services': services}


@app.route('/api/v1.0/services/<name>', method='GET')
def get_service(name):
    name = unquote(name)
    e = service(name)
    if not e:
        return service_not_found(name)
    # Do not check if service is running as we must be able to get info anyway
    return e


@app.route('/api/v1.0/services/<name>/stats', method='GET')
def get_service_stats(name):
    name = unquote(name)
    try:
        if not service(name):
            return service_not_found(name)
        if not service_is_running(name):
            return service_not_running(name)
        query = {
            "command": "stats",
            "properties": {
                "name": name
            }
        }
        stats = circus_client.call(query)
        pid = next(iter(stats['info'].keys()))
        resp = {
            "info": {
                "age": stats['info'][pid]['age'],
                "cpu": stats['info'][pid]['cpu'],
                "create_time": stats['info'][pid]['create_time'],
                "ctime": stats['info'][pid]['ctime'],
                "mem": stats['info'][pid]['mem'],
                "mem_info1": stats['info'][pid]['mem_info1'],
                "mem_info2": stats['info'][pid]['mem_info2'],
                "started": stats['info'][pid]['started'],
            },
            "name": stats['name'],
            "status": stats['status'],
            "time": stats['time'],
        }
    except Exception as e:
        resp = error(error_message=str(e))
    return resp


@app.route('/api/v1.0/services/<name>/status', method='GET')
def get_service_status(name):
    name = unquote(name)
    try:
        if not service(name):
            return service_not_found(name)
        query = {
            "command": "status",
            "properties": {
                "name": name
            }
        }
        status = circus_client.call(query)
        resp = {
            "name": name,
            "status": status['status'],
            "time": status['time'],
        }
    except Exception as e:
        resp = error(error_message=str(e))
    return resp


@app.route('/api/v1.0/services/<name>/start', method='PUT')
def start_service(name):
    name = unquote(name)
    try:
        if not service(name):
            return service_not_found(name)
        if service_is_running(name):
            return error(
                error_code=409,
                error_message=u"service {} is already running".format(name)
            )
        query = {
            "command": "start",
            "properties": {
                "name": name,
                "waiting": True
            }
        }
        start = circus_client.call(query)
        resp = {
            "name": name,
            "status": start['status'],
            "time": start['time'],
        }
    except Exception as e:
        resp = error(error_message=str(e))
    return resp


@app.route('/api/v1.0/services/<name>/stop', method='PUT')
def stop_service(name):
    name = unquote(name)
    try:
        if not service(name):
            return service_not_found(name)
        if not service_is_running(name):
            return service_not_running(name, already=True)
        query = {
            "command": "stop",
            "properties": {
                "name": name,
                "waiting": True
            }
        }
        stop = circus_client.call(query)
        resp = {
            "name": name,
            "status": stop['status'],
            "time": stop['time'],
        }
    except Exception as e:
        resp = error(error_message=str(e))
    return resp


@app.route('/api/v1.0/services/<name>/restart', method='PUT')
def restart_service(name):
    name = unquote(name)
    try:
        if not service_exists(name):
            return service_not_found(name)
        if not service_is_running(name):
            return service_not_running(name)
        query = {
            "command": "restart",
            "properties": {
                "name": name,
                "waiting": True
            }
        }
        restart = circus_client.call(query)
        resp = {
            "name": name,
            "status": restart['status'],
            "time": restart['time'],
        }
    except Exception as e:
        resp = error(error_message=str(e))
    return resp


@app.error(404)
def error404(error_data):
    # we have to define the content type ourselves and to use the
    # module json because bottle does not respect the content type on
    # error routings
    # https://github.com/bottlepy/bottle/issues/359
    response.content_type = 'application/json; charset=UTF-8'
    return json.dumps(
        error(
            error_code=404,
            error_message="the route is not valid. The documentation is on {}"
            .format(ONITU_READTHEDOCS)
        )
    )


@app.error(500)
def error500(error_data):
    response.content_type = 'application/json; charset=UTF-8'
    return json.dumps(
        error()
    )


if __name__ == '__main__':
    with ZeroMQHandler(get_logs_uri(session), multi=True).applicationbound():
        logger.info("Starting on http://{}:{}".format(host, port))
        run(app, host=host, port=port, quiet=True)
