# -*- coding: utf-8 -*-

from HTMLParser import HTMLParser

from .auth import S3Auth
from .request_factory import DeleteRequest, GetRequest, HeadRequest
from .request_factory import UploadRequest, UpdateMetadataRequest, CopyRequest
from .multipart_upload import MultipartUpload
from .key import Key


class Base(object):
    """
    The "Base" connection object, Handles the common S3 tasks
    (upload, copy, delete,etc)

    This is an "abstract" class, both Connection and Pool implement it.
    """

    def __init__(self, access_key, secret_key, default_bucket=None, tls=False,
                 endpoint="s3.amazonaws.com"):
        """
        Creates a new S3 connection

        Params:
            - access_key        AWS access key
            - secret_key        AWS secret key
            - default_bucket    (Optional) Sets the default bucket, so requests
              inside this pool won't have to specify
                                the bucket every time.
            - tls               (Optional) Make the requests using secure
              connection (Defaults to False)
            - endpoint          (Optional) Sets the s3 endpoint.

        """
        self.default_bucket = default_bucket
        self.auth = S3Auth(access_key, secret_key)
        self.tls = tls
        self.endpoint = endpoint

    def bucket(self, bucket):
        """
        Verifies that we have a bucket for a request

        Params:
            - bucket    The name of the bucket we're trying to use, None if we
              want to use the default bucket

        Returns:
            The bucket to use for the request

        Raises:
            ValueError if no bucket was provided AND no default bucket was
            defined.
        """
        b = bucket or self.default_bucket
        # If we don't have a bucket, raise an exception
        if not b:
            raise ValueError("You must specify a bucket in your request or set"
                             "the default_bucket for the connection")
        return b

    def get(self, key, bucket=None, headers=None):
        """
        Get a key from a bucket

        Params:
            - key           The key to get

            - bucket        (Optional) The name of the bucket to use
            (can be skipped if setting the default_bucket)
            - headers       (Optional) Additional headers of the request

        Returns:
            - A response object from the requests lib or a future that wraps
            that response object if used with a pool.

        Usage:

        >>> conn.get('my_awesome_key.zip','sample_bucket')

        """
        r = GetRequest(self, key, self.bucket(bucket), headers=headers)
        return self.run(r)

    def get_key(self, filename, bucket=None):
        """Unlike get(), gets information about a key. It returns a Key object.
        At the moment, the key object doesn't include contents. Use get() to
        acknowledge these."""
        # Setting prefix to the key filename and max keys to 1 to minimize
        # output.
        params = {'prefix': filename, 'max-keys': 1}
        keys = self.list_keys(bucket, params)
        for key in keys:  # should be the first result, but that's a generator
            if key.key == filename:
                return key
        # if not found
        return None

    def upload(self, key, local_file,
               bucket=None, expires=None, content_type=None,
               public=True, headers=None, rewind=True, close=False):
        """
        Upload a file and store it under a key

        Params:
            - key           The key to store the file under.

            - local_file    A file-like object which would be uploaded

            - bucket        (Optional) The name of the bucket to use (can be
              skipped if setting the default_bucket)
                            option for the connection

            - expires       (Optional) Sets the the Cache-Control headers. The
              value can be a number (used as seconds),
                            A Timedelta or the 'max' string, which will
                            automatically set the file to be cached for a
                            year. Defaults to no caching

            - content_type  (Optional) Explicitly sets the Content-Type header.
              if not specified, tinys3 will try to guess the right content type
              for the file (using the mimetypes lib)

            - public        (Optional) If set to true, tinys3 will set the file
              to be publicly available using the acl headers. Defaults to True.

            - headers       (Optional) Allows you to specify extra headers for
              the request using a dict.

            - rewind        (Optional) If true, tinys3 will seek the file like
              object to the beginning before uploading. Defaults to True.

            - Close         (Optional) If true, tinys3 will close the file like
              object after the upload was complete

        Returns:
            - A response object from the requests lib or a future that wraps
              that response object if used with a pool.

        Usage:

        >>> with open('my_local_file.zip', 'rb') as f:
        >>>     conn.upload('my_awesome_key.zip',f,
        >>>                 expires='max',
        >>>                 bucket='sample_bucket',
        >>>                 headers={
        >>>                     'x-amz-storage-class': 'REDUCED_REDUNDANCY'
        >>>                 })

        There are more usage examples in the readme file.

        """
        r = UploadRequest(self, key, local_file, self.bucket(bucket),
                          expires=expires, content_type=content_type,
                          public=public, extra_headers=headers, rewind=rewind,
                          close=close)
        return self.run(r)

    def copy(self, from_key, from_bucket, to_key, to_bucket=None,
             metadata=None, public=True):
        """
        Copy a key contents to another key/bucket with an option to update
        metadata/public state

        Params:
            - from_key      The source key
            - from_bucket   The source bucket
            - to_key        The target key
            - to_bucket     (Optional) The target bucket, if not specified,
              tinys3 will use the `from_bucket`
            - metadata      (Optional) Allows an override of the new key's
              metadata. if not defined, tinys3 will copy the source key's
              metadata.
            - public        (Optional) Same as upload, should the new key be
              publicly accessible? Default to True.

        Returns:
            - A response object from the requests lib or a future that wraps
              that response object if used with a pool.

        Usage:
            >>> conn.copy('source_key.jpg','source_bucket','target_key.jpg',
                          'target_bucket',
                       metadata={ 'x-amz-storage-class': 'REDUCED_REDUNDANCY'})

        There are more usage examples in the readme file.
        """
        to_bucket = self.bucket(to_bucket or from_bucket)
        r = CopyRequest(self, from_key, from_bucket, to_key, to_bucket,
                        metadata=metadata, public=public)
        return self.run(r)

    def update_metadata(self, key, metadata=None, bucket=None, public=True):
        """
        Updates the metadata information for a file

        Params:
            - key           The key to update
            - metadata      (Optional) The metadata dict to set for the key
            - public        (Optional) Same as upload, should the key be
              publicly accessible? Default to True.

        Returns:
            - A response object from the requests lib or a future that wraps
              that response object if used with a pool.

        Usage:
            >>> conn.update_metadata('key.jpg',
                {'x-amz-storage-class': 'REDUCED_REDUNDANCY'},'my_bucket')

        There are more usage examples in the readme file.
        """
        r = UpdateMetadataRequest(self, key, self.bucket(bucket), metadata,
                                  public)

        return self.run(r)

    def delete(self, key, bucket=None):
        """
        Delete a key from a bucket

        Params:
            - key           The key to delete

            - bucket        (Optional) The name of the bucket to use (can be
              skipped if setting the default_bucket) for the connection

        Returns:
            - A response object from the requests lib or a future that wraps
              that response object if used with a pool.

        Usage:

        >>> conn.delete('my_awesome_key.zip','sample_bucket')

        """
        r = DeleteRequest(self, key, self.bucket(bucket))
        return self.run(r)

    def run(self, request):
        """
        Executes an S3Request and returns the result

        Params:
            - request An instance of S3Request

        """
        return self._handle_request(request)

    def head_bucket(self, bucket=None):
        r = HeadRequest(self, self.bucket(bucket))
        return self.run(r)

    def list_keys(self, bucket=None, extra_params=None):
        """Generator to list all existing keys on the connection's bucket
        (or the given one). The following params can be used :
        - prefix: only list keys whose name begins with the specified
                  prefix.
        - delimiter: All keys that contain the same string between the prefix,
                     if specified, and the first occurrence of the delimiter
                     after the prefix are grouped under a single result
                     element, CommonPrefixes.
        - encoding-type: Use 'url' ton encode the response.
        - max-keys: Sets the maximum number of keys,
                    from 1 to 1,000, to return in the response body.
                    1,000 is the maximum number of keys that can be
                    returned in a response (default 1,000).
        - marker: Specifies the key to start with when listing objects in a
                  bucket. Amazon S3 lists objects in alphabetical order.
        """
        params = {}
        if extra_params is not None:
            params.update(extra_params)
        more_results = True
        while more_results:
            # GET /params
            req = GetRequest(self, '', self.bucket(bucket),
                             query_params=params)
            rep = self.run(req)
            parser = self.UploadIdParser()
            parser.feed(rep.text)
            for key_dict in parser.keys:
                key = Key(parser.data['name'], key_dict)
                yield key
            if parser.data['istruncated'] == 'true':
                params['marker'] = parser.keys[-1]['key']
            else:
                more_results = False

    def list_multipart_uploads(self, bucket=None, extra_params=None):
        """Generator to list all existing multipart uploads on the connection's
        bucket (or the given one). The following params can be used :
        - prefix: only list uploads for the keys that begin with the specified
                  prefix.
        - delimiter: All keys that contain the same string between the prefix,
                     if specified, and the first occurrence of the delimiter
                     after the prefix are grouped under a single result
                     element.
        - encoding-type: Use 'url' ton encode the response.
        - max-uploads: Sets the maximum number of multipart uploads,
                       from 1 to 1,000, to return in the response body.
                       1,000 is the maximum number of uploads that can be
                       returned in a response (default 1,000).
        - key-marker: Together with upload-id-marker, this parameter specifies
                      the multipart upload after which listing should begin.
        - upload-id-marker: Together with key-marker, specifies the multipart
                            upload after which listing should begin.
        """
        params = {'uploads': None}
        if extra_params is not None:
            params.update(extra_params)
        more_results = True
        while more_results:
            # GET /?uploads&params
            req = GetRequest(self, '', self.bucket(bucket),
                             query_params=params)
            rep = self.run(req)
            parser = self.UploadIdParser()
            parser.feed(rep.text)
            for upload in parser.uploads:
                mp = MultipartUpload(self, parser.data['bucket'],
                                     upload['key'])
                mp.uploadId = upload['uploadid']
                yield mp
            if parser.data['istruncated'] == 'true':
                params['key-marker'] = parser.data['nextkeymarker']
                params['upload-id-marker'] = parser.data['nextuploadidmarker']
            else:
                more_results = False

    def get_all_multipart_uploads(self, bucket=None, extra_params=None):
        """The non-generator version of list_multipart_uploads."""
        mps = [mp for mp in self.list_multipart_uploads(bucket, extra_params)]
        return mps

    def initiate_multipart_upload(self, key, bucket=None):
        """Returns a "boto-ish" MultipartUpload object that works kind of
        the same way than the Boto one."""
        mp = MultipartUpload(self, bucket, key)
        mp.initiate()
        return mp

    def _handle_request(self, request):
        """
        An abstract method, to be implemented by inheriting classes
        """
        raise NotImplementedError


class Connection(Base):
    """
    The basic implementation of an S3 connection.
    """

    class UploadIdParser(HTMLParser):
        """An internal HTML parser to parse server responses.
        This shouldn't be of any use outside of the class."""

        def __init__(self):
            HTMLParser.__init__(self)
            self.currentTag = None
            # All parsed data goes in that dict by default
            self.data = {}
            # For list_multipart_uploads
            self.uploads = []
            self.currentUpload = {}
            self.inUpload = False
            # For MultipartUpload.list_parts
            self.parts = []
            self.currentPart = {}
            self.inPart = False
            # For list_keys
            self.keys = []
            self.currentKey = {}
            self.inKey = False

        def handle_starttag(self, tag, attrs):
            self.currentTag = tag
            # When listing multipart uploads, information about multipart
            # upload is enclosed in <Upload> tags. We have to keep trace of it.
            # NB: The parser automatically lowercases all tags.
            if tag == 'upload':
                self.inUpload = True
                self.currentUpload = {}
            elif tag == 'part':
                self.inPart = True
                self.currentPart = {}
            elif tag == 'contents':  # contents means a new key
                self.inKey = True
                self.currentKey = {}

        def handle_endtag(self, tag):
            self.currentTag = None
            if tag == 'upload':
                self.inUpload = False
                self.uploads.append(self.currentUpload)
                self.currentUpload = {}
            elif tag == 'part':
                self.inPart = False
                self.parts.append(self.currentPart)
                self.currentPart = {}
            elif tag == 'contents':
                self.inKey = False
                self.keys.append(self.currentKey)
                self.currentKey = {}

        def handle_data(self, data):
            try:
                if self.inUpload:
                    self.currentUpload[self.currentTag] = data
                elif self.inPart:
                    self.currentPart[self.currentTag] = data
                elif self.inKey:
                    self.currentKey[self.currentTag] = data
                else:
                    self.data[self.currentTag] = data
            except KeyError:
                # As Amazon answers XML, the parser may call an empty
                # handle_data before 'real' HTML parsing
                # (self.currentTag still None). But it's harmless
                pass

        def upload_id(self):
            return self.data['uploadid']

        def key(self):
            return self.data['key']

        def bucket(self):
            return self.data['bucket']

    def _handle_request(self, request):
        """
        Implements S3Request execution.

        Params:
            - request       S3Request object to run

        """
        return request.run()
