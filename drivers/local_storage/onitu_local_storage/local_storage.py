import os
import json
import tempfile

from onitu.plug import Plug, DriverError, ServiceError
from onitu.escalator.client import EscalatorClosed
from onitu.utils import IS_WINDOWS, u, log_traceback

if IS_WINDOWS:
    import threading

    import win32api
    import win32file
    import win32con
else:
    import pyinotify

TMP_EXT = '.onitu-tmp'

plug = Plug()


def to_tmp(filename):
    return os.path.join(
        os.path.dirname(filename), '.' + os.path.basename(filename) + TMP_EXT
    )


def walkfiles(root):
    return (
        os.path.join(dirpath, f)
        for dirpath, _, files in os.walk(root) for f in files
    )


def update(metadata, mtime=None):
    if os.path.exists(to_tmp(metadata.path)):
        return;

    try:
        metadata.size = os.path.getsize(metadata.path)

        if not mtime:
            mtime = os.path.getmtime(metadata.path)
        metadata.extra['revision'] = mtime
    except (IOError, OSError) as e:
        raise ServiceError(
            u"Error updating file '{}': {}".format(metadata.path, e)
        )
    else:
        plug.update_file(metadata)
        set_status(metadata.path, 'synced')


def delete(metadata):
    plug.delete_file(metadata)
    set_status(metadata.path, None)


def move(old_metadata, new_filename):
    new_metadata = plug.move_file(old_metadata, new_filename)
    new_metadata.extra['revision'] = os.path.getmtime(new_filename)
    # We update the size in case the file was moved very quickly after a change
    # so the old metadata are not up-to-date
    new_metadata.size = os.path.getsize(new_metadata.path)
    new_metadata.write()
    set_status(old_metadata.path, None)
    set_status(new_metadata.path, 'synced')


def check_changes(folder):
    expected_files = set()

    expected_files.update(plug.list(folder).keys())

    for path in walkfiles(folder.path):
        if os.path.splitext(path)[1] == TMP_EXT:
            continue

        filename = folder.relpath(path)

        expected_files.discard(filename)

        metadata = plug.get_metadata(filename, folder)
        revision = metadata.extra.get('revision', 0.)

        try:
            mtime = os.path.getmtime(path)
        except (IOError, OSError) as e:
            raise ServiceError(
                u"Error updating file '{}': {}".format(metadata.path, e)
            )
            mtime = 0.

        if mtime > revision:
            update(metadata, mtime)
        else:
            set_status(metadata.path, "synced")

    for filename in expected_files:
        metadata = plug.get_metadata(filename, folder)
        # If we don't see this file and we're not uptodate, this could
        # mean that we simply never transfered it, so we shouldn't trigger
        # a deletion (cf https://github.com/onitu/onitu/issues/130)
        if plug.name in metadata.uptodate:
            plug.delete_file(metadata)


def set_status(abs_path, status):

    tmp_dir = tempfile.gettempdir()
    tmp_filename = tmp_dir + os.sep + 'onitu_synced_files'

    try:
        with open(tmp_filename, "r") as jsonFile:
            data = json.load(jsonFile)
    except IOError as e:
        data = dict()

    if status is None:
        data.pop(abs_path, None)
    else:
        data[abs_path] = status

    try:
        with open(tmp_filename, "w") as jsonFile:
            jsonFile.write(json.dumps(data, indent = 4))
    except IOError as e:
        raise ServiceError(
            u"Error to write in status file '{}': {}".format(tmp_filename, e)
        )


@plug.handler()
def normalize_path(p):
    normalized = os.path.normpath(os.path.expanduser(p))
    if not os.path.isabs(normalized):
        raise DriverError(u"The folder path '{}' is not absolute.".format(p))

    return normalized


@plug.handler()
def get_chunk(metadata, offset, size):
    try:
        with open(metadata.path, 'rb') as f:
            f.seek(offset)
            return f.read(size)
    except (IOError, OSError) as e:
        raise ServiceError(
            u"Error getting file '{}': {}".format(metadata.path, e)
        )


@plug.handler()
def start_upload(metadata):
    tmp_file = to_tmp(metadata.path)

    try:
        try:
            os.makedirs(os.path.dirname(tmp_file))
        except OSError:
            pass

        open(tmp_file, 'wb').close()
        set_status(metadata.path, "pending")

        if IS_WINDOWS:
            win32api.SetFileAttributes(
                tmp_file, win32con.FILE_ATTRIBUTE_HIDDEN)
    except IOError as e:
        raise ServiceError(
            u"Error creating file '{}': {}".format(tmp_file, e)
        )


@plug.handler()
def upload_chunk(metadata, offset, chunk):
    tmp_file = to_tmp(metadata.path)

    try:
        with open(tmp_file, 'r+b') as f:
            f.seek(offset)
            f.write(chunk)
    except (IOError, OSError) as e:
        raise ServiceError(
            u"Error writting file '{}': {}".format(tmp_file, e)
        )


@plug.handler()
def end_upload(metadata):
    tmp_file = to_tmp(metadata.path)

    try:
        if IS_WINDOWS:
            # On Windows we can't move a file
            # if dst exists
            try:
                os.unlink(metadata.path)
            except OSError:
                pass
        os.rename(tmp_file, metadata.path)
        mtime = os.path.getmtime(metadata.path)

        if IS_WINDOWS:
            win32api.SetFileAttributes(
                metadata.path, win32con.FILE_ATTRIBUTE_NORMAL)
    except (IOError, OSError) as e:
        raise ServiceError(
            u"Error for file '{}': {}".format(metadata.path, e)
        )

    metadata.extra['revision'] = mtime
    metadata.write()

    set_status(metadata.path, "synced")


@plug.handler()
def abort_upload(metadata):
    tmp_file = to_tmp(metadata.path)
    try:
        os.unlink(tmp_file)
    except (IOError, OSError) as e:
        raise ServiceError(
            u"Error deleting file '{}': {}".format(tmp_file, e)
        )

    set_status(metadata.path, None)

@plug.handler()
def delete_file(metadata):
    try:
        os.unlink(metadata.path)
    except (IOError, OSError) as e:
        raise ServiceError(
            u"Error deleting file '{}': {}".format(metadata.path, e)
        )
    set_status(metadata.path, None)


@plug.handler()
def move_file(old_metadata, new_metadata):
    try:
        os.renames(old_metadata.path, new_metadata.path)
    except (IOError, OSError) as e:
        raise ServiceError(
            u"Error moving file '{}': {}".format(old_metadata.path, e)
        )

    set_status(old_metadata.path, None)
    set_status(old_metadata.path, "synced")


if IS_WINDOWS:
    FILE_LIST_DIRECTORY = 0x0001

    def win32watcherThread(root, file_lock):
        dirHandle = win32file.CreateFile(
            root,
            FILE_LIST_DIRECTORY,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
            None,
            win32con.OPEN_EXISTING,
            win32con.FILE_FLAG_BACKUP_SEMANTICS,
            None
        )

        actions_names = {
            1: 'create',
            2: 'delete',
            3: 'write',
            4: 'delete',
            5: 'write'
        }

        while True:
            results = win32file.ReadDirectoryChangesW(
                dirHandle,
                1024,
                True,
                win32con.FILE_NOTIFY_CHANGE_FILE_NAME |
                win32con.FILE_NOTIFY_CHANGE_DIR_NAME |
                win32con.FILE_NOTIFY_CHANGE_ATTRIBUTES |
                win32con.FILE_NOTIFY_CHANGE_SIZE |
                win32con.FILE_NOTIFY_CHANGE_LAST_WRITE |
                win32con.FILE_NOTIFY_CHANGE_SECURITY,
                None
            )

            for action, file_ in results:
                abs_path = root / file_

                if (abs_path.isdir() or abs_path.ext == TMP_EXT or
                    not (win32api.GetFileAttributes(abs_path)
                         & win32con.FILE_ATTRIBUTE_NORMAL)):
                    continue

                with file_lock:
                    if actions_names.get(action) == 'write':
                        filename = root.relpathto(abs_path)

                        try:
                            metadata = plug.get_metadata(filename)
                            update(metadata, abs_path)
                        except EscalatorClosed:
                            return

    def watch_changes(folder):
        file_lock = threading.Lock()
        notifier = threading.Thread(target=win32watcherThread,
                                    args=(folder.path, file_lock))
        notifier.setDaemon(True)
        notifier.start()
else:
    class Watcher(pyinotify.ProcessEvent):
        def __init__(self, folder, *args, **kwargs):
            super(Watcher, self).__init__(*args, **kwargs)

            self.folder = folder

        def process_IN_CLOSE_WRITE(self, event):
            self.process_event(event.pathname, update)

        def process_IN_DELETE(self, event):
            self.process_event(event.pathname, delete)

        def process_IN_MOVED_TO(self, event):
            if event.dir:
                for new in walkfiles(event.pathname):
                    if hasattr(event, 'src_pathname'):
                        old = new.replace(event.pathname, event.src_pathname)
                        self.process_event(old, move, u(new))
                    else:
                        self.process_event(new, update)
            else:
                if hasattr(event, 'src_pathname'):
                    self.process_event(
                        event.src_pathname, move, u(event.pathname)
                    )
                else:
                    self.process_event(event.pathname, update)

        def process_event(self, filename, callback, *args):
            filename = os.path.relpath(u(filename), self.folder.path)

            if os.path.splitext(filename)[1] == TMP_EXT:
                return

            try:
                metadata = plug.get_metadata(filename, self.folder)
                callback(metadata, *args)
            except EscalatorClosed:
                pass
            except OSError as e:
                plug.logger.error("Error when dealing with FS event: {}", e)
            except (DriverError, ServiceError) as e:
                plug.logger.error(str(e))
            except Exception:
                log_traceback(plug.logger)

    def watch_changes(folder):
        manager = pyinotify.WatchManager()
        notifier = pyinotify.ThreadedNotifier(manager, Watcher(folder))
        notifier.daemon = True
        notifier.start()

        mask = (pyinotify.IN_CREATE | pyinotify.IN_CLOSE_WRITE |
                pyinotify.IN_DELETE | pyinotify.IN_MOVED_TO |
                pyinotify.IN_MOVED_FROM)
        manager.add_watch(folder.path, mask, rec=True, auto_add=True)


def start():
    for folder in plug.folders_to_watch:
        try:
            os.makedirs(folder.path)
        except OSError:
            # can be raised if the folder already exists
            pass

        if not os.path.exists(folder.path):
            raise DriverError(
                u"Cannot create the folder '{}': {}".format(folder.path)
            )

        watch_changes(folder)
        check_changes(folder)

    plug.listen()
