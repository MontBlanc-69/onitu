import time
import urllib3
import threading

import dropbox
from dropbox.session import DropboxSession
from dropbox.client import DropboxClient

from onitu.plug import Plug
from onitu.plug import DriverError, ServiceError
from onitu.escalator.client import EscalatorClosed
from onitu.utils import u, b  # Unicode helpers

# Onitu has a unique set of App key and secret to identify it.
ONITU_APP_KEY = u"6towoytqygvexx3"
ONITU_APP_SECRET = u"90hsd4z4d8eu3pp"
ONITU_ACCESS_TYPE = u"dropbox"  # full access to user's Dropbox (may change)
# something like "Sat, 21 Aug 2010 22:31:20 +0000"
TIMESTAMP_FMT = u"%a, %d %b %Y %H:%M:%S +0000"
plug = Plug()
dropbox_client = None


def connect_client():
    """Helper function to connect to Dropbox via the API, using access token
    keys to authenticate Onitu."""
    global dropbox_client
    plug.logger.debug(u"Attempting Dropbox connection using Onitu credentials")
    sess = DropboxSession(ONITU_APP_KEY,
                          ONITU_APP_SECRET,
                          ONITU_ACCESS_TYPE)
    # Use the OAuth access token previously retrieved by the user and typed
    # into Onitu configuration.
    sess.set_token(u(plug.options[u'access_key']),
                   u(plug.options[u'access_secret']))
    dropbox_client = DropboxClient(sess)
    plug.logger.debug(u"Dropbox connection with Onitu credentials successful")
    return dropbox_client


def root_prefixed_filename(filename):
    name = u(plug.options[u'root'])
    if not name.endswith(u'/'):
        name += u'/'
    name += u(filename)
    return name


def remove_upload_id(metadata):
    up_id = metadata.extra.get(u'upload_id', None)
    if up_id is not None:
        plug.logger.debug(u"Removing upload ID '{}' from '{}' metadata"
                          .format(up_id, metadata.filename))
        del metadata.extra[u'upload_id']


def update_metadata_info(metadata, new_metadata, write=True):
    """Updates important metadata informations, e.g. on upload ending or
    change detection. We sometimes don't want to write immediately, e.g. when
    plug.update_file is called right after."""
    metadata.size = new_metadata[u'bytes']
    metadata.extra[u'rev'] = new_metadata[u'rev']
    metadata.extra[u'modified'] = new_metadata[u'modified']
    if write:
        metadata.write()


def remove_conflict(filename):
    conflict_name = u'conflict:{}'.format(filename)
    conflict = plug.entry_db.get(conflict_name, default=None)
    if conflict:
        plug.entry_db.delete(conflict_name)


def conflicting_filename(filename, value=False):
    """Check for name conflicts in the DB.
    If value is True, search in values (Dropbox filenames)
    instead of Onitu ones."""
    confs = plug.entry_db.range(u'conflict:')
    # Remove the leading 'conflict:' substring
    # do str(o_fn) to manage Python 3
    confs = [(str(o_fn).split(u':', 1)[1], d_fn) for (o_fn, d_fn) in confs]
    conflict_name = None
    # 0 = Onitu filename; 1 = Dropbox filename
    if value:
        searched = 1
    else:
        searched = 0
    for filenames in confs:
        if filenames[searched] == filename:
            conflict_name = filenames[int(not searched)]
            plug.logger.warning(u"Case conflict on Dropbox, mapping "
                                u"modifications of file {} to "
                                u"file {}, please rename it!"
                                .format(filename, conflict_name))
            break
    return conflict_name


def stringify(s):
    """In Py3k, unicode are strings, so we mustn't encode it.
    However it is necessary in Python 2.x, since Unicode strings are
    unicode, not str."""
    if type(s) != str:
        s = b(s)
    return s


def get_dropbox_filename(metadata):
    """Get the dropbox filename based on the Onitu filename.
    Usually it's the same but we have to check for name conflicts"""
    filename = root_prefixed_filename(metadata.filename)
    # Check if this file is in naming conflict with Dropbox. If that's the case
    # tell Dropbox we update its remote file, not the Onitu's file name
    conflict_name = conflicting_filename(filename)
    if conflict_name:
        filename = conflict_name
    return filename


@plug.handler()
def get_chunk(metadata, offset, size):
    filename = get_dropbox_filename(metadata)
    plug.logger.debug(u"Getting chunk of size {} from file {}"
                      u" from offset {} to {}"
                      .format(size, filename, offset, offset+(size-1)))
    try:
        # content_server = True is required to let us access to file contents,
        # not only metadata
        url, params, headers = dropbox_client.request(u"/files/dropbox/{}"
                                                      .format(filename),
                                                      method=u"GET",
                                                      content_server=True)
        # Using the 'Range' HTTP Header for offseting.
        headers[u'Range'] = u"bytes={}-{}".format(offset, offset+(size-1))
        chunk = dropbox_client.rest_client.request(u"GET",
                                                   url,
                                                   headers=headers,
                                                   raw_response=True)
    except (dropbox.rest.ErrorResponse, dropbox.rest.RESTSocketError) as err:
        raise ServiceError(u"Cannot get chunk of '{}' - {}"
                           .format(filename, err))
    plug.logger.debug(u"Getting chunk of size {} from file {}"
                      u" from offset {} to {} - Done"
                      .format(size, filename, offset, offset+(size-1)))
    return chunk.read()


@plug.handler()
def upload_chunk(metadata, offset, chunk):
    filename = get_dropbox_filename(metadata)
    # Get the upload id of this file. None = upload's first time
    up_id = metadata.extra.get(u'upload_id', None)
    plug.logger.debug(u"Uploading chunk of size {}"
                      u" to file {} at offset {} - Upload ID: {}"
                      .format(len(chunk), filename, offset, up_id))
    # upload_chunk returns a tuple containing the offset and the upload ID of
    # this upload. The offset isn't very useful
    try:
        (_, up_id) = dropbox_client.upload_chunk(file_obj=chunk,
                                                 length=len(chunk),
                                                 offset=offset,
                                                 upload_id=up_id)
    except (dropbox.rest.ErrorResponse, dropbox.rest.RESTSocketError) as err:
        raise ServiceError(u"Unable to upload chunk of {}: {}"
                           .format(filename, err))
    if metadata.extra.get(u'upload_id', None) != up_id:
        metadata.extra[u'upload_id'] = up_id
        metadata.write()
        plug.logger.debug(u"Storing upload ID {} in metadata".format(up_id))
    plug.logger.debug(u"Uploading chunk of size {}"
                      u" to file {} at offset {} - Done"
                      .format(len(chunk), filename, offset))


@plug.handler()
def end_upload(metadata):
    # Check if this file is in naming conflict with Dropbox. If that's the case
    # tell Dropbox we update its remote file, not the Onitu's file name
    filename = get_dropbox_filename(metadata)
    plug.logger.debug(u"Ending upload of '{}'".format(filename))
    # Note: dropbox_client (the global variable) != dropbox.client,
    # the access to the dropbox.client submodule
    path = u"/commit_chunked_upload/{}/{}".format(
        dropbox_client.session.root,
        dropbox.client.format_path(filename)
        )
    up_id = metadata.extra.get(u'upload_id', None)
    # At this point we should have the upload ID.
    if up_id is None:
        # empty file. We must upload one.
        if metadata.size == 0:
            (_, upload_id) = dropbox_client.upload_chunk(u'', 0)
            up_id = upload_id
        else:
            raise DriverError(u"No upload ID for {}".format(filename))
    # NEVER overwrite things, unless we're up-to-date
    # NO autorename, because we don't want duplications on Onitu ! We report
    # an error to the user instead.
    params = dict(overwrite=False, upload_id=up_id)
    # Upload revision Onitu has of this file. The revision has to be the most
    # recent one, else Dropbox will send us a conflict error. No rev if this
    # file isn't on dropbox yet
    rev = metadata.extra.get(u'rev', None)
    if rev:
        params[u'parent_rev'] = rev
    try:
        url, params, headers = dropbox_client.request(path,
                                                      params,
                                                      content_server=True)
        resp = dropbox_client.rest_client.POST(url, params, headers)
    # Among most likely error causes are :
    # - bad rev: the file revision Onitu wasn't the most up-to-date one. We
    #   then have to wait that Onitu re-sync itself with Dropbox before
    #   attempting this upload again.
    except (dropbox.rest.ErrorResponse, dropbox.rest.RESTSocketError) as err:
        raise ServiceError(u"Cannot commit chunked upload for '{}' - {}"
                           .format(filename, err))
    remove_upload_id(metadata)
    update_metadata_info(metadata, resp)
    # A naming conflict occurred
    # we have to state it in the driver's conflicts list
    if resp[u'path'] != filename:
        plug.entry_db.put(u'conflict:{}'.format(filename), resp[u'path'])
        plug.logger.warning(u"Case conflict on Dropbox!! Onitu file {} is now "
                            u"mapped to Dropbox file {}, please rename it!"
                            .format(filename, resp[u'path']))
    plug.logger.debug(u"Storing revision {} for '{}'"
                      .format(resp[u'rev'], filename))
    plug.logger.debug(u"Ending upload of '{}' - Done".format(filename))


@plug.handler()
def abort_upload(metadata):
    remove_upload_id(metadata)
    metadata.write()


@plug.handler()
def move_file(old_metadata, new_metadata):
    old_filename = get_dropbox_filename(old_metadata)
    new_filename = get_dropbox_filename(new_metadata)
    plug.logger.debug(u"Moving '{}' to '{}'".format(old_filename,
                                                    new_filename))
    # while is to manage cases where we have to try again.
    for attempt in range(10):
        try:
            new_db_metadata = dropbox_client.file_move(
                from_path=stringify(old_filename),
                to_path=stringify(new_filename))
        except dropbox.rest.ErrorResponse as err:
            if err.status is 503 and u"please re-issue request" in err.error:
                # If we didn't exceed retry limit
                if attempt != 9:
                    plug.logger.info(u"Retrying to move file {} to {}"
                                     .format(old_filename, new_filename))
                    continue  # as requested, try again
            elif err.status is 403 and err.error.startswith(
                    u"A file with that name already exists"
            ):
                # In case of already existing file or case conflict,
                # when moving, Dropbox doesn't rename and always throws an
                # error. So if an error arised, tell the database Onitu new
                # file is still in conflict with the old Dropbox one
                # (hasn't been removed)
                plug.entry_db.put(u'conflict:{}'.format(new_filename),
                                  old_filename)
                plug.logger.warning(u"Case conflict on Dropbox!! Onitu file {}"
                                    u" is mapped to Dropbox file {}, "
                                    u"please rename it!"
                                    .format(new_filename, old_filename))
            raise ServiceError(u"Cannot move file {} - {}"
                               .format(old_filename, err))
        break
    remove_conflict(old_filename)
    # Don't forget to update infos of the new metadata to avoid an useless
    # transfer the other way around
    plug.logger.debug(u"Storing revision '{}' for file '{}'"
                      .format(new_db_metadata[u'rev'], new_filename))
    update_metadata_info(new_metadata, new_db_metadata)
    plug.logger.debug(u"Moving '{}' to '{}' - Done"
                      .format(old_filename, new_filename))


@plug.handler()
def delete_file(metadata):
    filename = get_dropbox_filename(metadata)
    plug.logger.debug(u"Deleting '{}'".format(filename))
    try:
        dropbox_client.file_delete(stringify(filename))
    except dropbox.rest.ErrorResponse as err:
        raise ServiceError(u"Cannot delete file {} - {}".format(filename, err))
    remove_conflict(filename)
    plug.logger.debug(u"Deleting '{}' - Done".format(filename))


class CheckChanges(threading.Thread):
    """A class spawned in a thread to poll for changes on the Dropbox account.
    Dropbox works with deltas so we have to periodically send a request to
    Dropbox with our current delta to know what has changed since we retrieved
    it."""

    def __init__(self, timer):
        threading.Thread.__init__(self)
        self.stop = threading.Event()
        self.timer = timer
        # Cursors are used for Dropbox deltas to know from which point it must
        # send changes. A cursor is returned at each delta request, and one
        # ought to send the previous cursor to receive only the changes that
        # have occurred since the last time.
        self.cursor = plug.entry_db.get(u'dropbox_cursor', default=None)
        plug.logger.debug(u"Getting cursor '{}' out of database"
                          .format(self.cursor))

    def check_dropbox(self):
        global plug
        plug.logger.debug(u"Checking dropbox for changes in '{}' folder"
                          .format(plug.options[u'root']))
        prefix = plug.options[u'root']
        # Dropbox lists filenames with a leading slash
        if not prefix.startswith(u'/'):
            prefix = u'/' + prefix
        has_more = True
        changes = None
        while has_more:
            # Dropbox doesn't support trailing slashes
            if prefix.endswith(u'/'):
                prefix = prefix[:-1]
            changes = dropbox_client.delta(cursor=self.cursor,
                                           path_prefix=prefix)
            plug.logger.debug(u"Processing {} entries"
                              .format(len(changes[u'entries'])))
            prefix += u'/'  # put the trailing slash back
            # Entries are a list of couples (filename, metadatas).
            # However, the filename is case-insensitive. So we have to use the
            # 'path' field of the metadata containing the true, correct-case
            # filename.
            for (db_filename, db_metadata) in changes[u'entries']:
                # No metadata = deleted file.
                if db_metadata is None:
                    # Retrieve the metadata saved by Dropbox.
                    db_metadata = dropbox_client.metadata(db_filename)
                # Check for conflicts with Dropbox filename. If yes, retrieve
                # the onitu filename (thanks to value flag)
                filename = conflicting_filename(db_metadata[u'path'],
                                                value=True)
                if not filename:  # no conflict
                    filename = db_metadata[u'path']
                # Strip the root in the Dropbox filename for root coherence.
                rootless_filename = filename[len(prefix):]
                plug.logger.debug(u"Getting metadata of file '{}'"
                                  .format(rootless_filename))
                # empty string = root; ignore it
                # ignore directories as well (onitu creates them on the fly)
                if (rootless_filename == u'' or
                   db_metadata.get(u'is_dir', False)):
                    continue
                metadata = plug.get_metadata(rootless_filename)
                # If the file has been deleted
                if db_metadata.get(u'is_deleted', False) is True:
                    # If it was synchronized by Onitu, notify the Plug
                    if metadata is not None:
                        plug.logger.debug(u"Delete detected on Dropbox of "
                                          u"file {}".format(metadata.filename))
                        plug.delete_file(metadata)
                    continue
                # Get the timestamps
                onitu_ts = metadata.extra.get(u'modified', None)
                if onitu_ts:
                    onitu_ts = time.strptime(onitu_ts,
                                             TIMESTAMP_FMT)
                dropbox_ts = time.strptime(db_metadata[u'modified'],
                                           TIMESTAMP_FMT)
                # If Dropbox timestamp is more recent
                if not onitu_ts or dropbox_ts > onitu_ts:
                    plug.logger.debug(u"Updating metadata of file '{}'"
                                      .format(rootless_filename))
                    # write=False because metadata.write() will be performed
                    # anyway in plug.update_file
                    update_metadata_info(metadata, db_metadata, write=False)
                    plug.update_file(metadata)
            # 'has_more' is set when where Dropbox couldn't send all data
            # in one shot. It's a special case where we're allowed to
            # immediately do another delta with the same cursor to retrieve
            # the remaining data.
            has_more = changes[u'has_more']
        plug.logger.debug(u"New cursor: '{}' Old cursor: '{}'"
                          .format(changes[u'cursor'], self.cursor))
        if changes[u'cursor'] != self.cursor:
            plug.entry_db.put(u'dropbox_cursor', changes[u'cursor'])
            plug.logger.debug(u"Setting '{}' as new cursor"
                              .format(changes[u'cursor']))
            self.cursor = changes[u'cursor']

    def run(self):
        while not self.stop.isSet():
            try:
                self.check_dropbox()
            except urllib3.exceptions.MaxRetryError as mre:
                plug.logger.error(u"Cannot poll changes on Dropbox - {}"
                                  .format(mre))
            except EscalatorClosed:
                # We are closing
                return
            self.stop.wait(self.timer)

    def stop(self):
        self.stop.set()


def start(*args, **kwargs):
    if plug.options[u'changes_timer'] < 0:
        raise DriverError(
            u"The change timer option must be a positive integer")
    connect_client()
    # Start the watching-for-new-files thread
    check = CheckChanges(plug.options[u'changes_timer'])
    check.daemon = True
    check.start()
    plug.listen()
