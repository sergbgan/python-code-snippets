#!/usr/bin/env python
# coding: utf-8

"""
    wallet info
    ~~~~~~~~~~~

    Hacked python script to create a simple HTML information file for
    bitcoin wallet.dat files.

    Works on many bitcoin forks e.g.: litecoin, PPCoin, Feathercoin but
    will still use links to bitcoin info pages :(

    The generated HTML file lists all used wallet addresses with links to:
            blockchain.info
            blockexplorer.com

    Just add filenames to script file, e.g:

    $ python wallet_info.py my_wallets/wallet_bak1.dat  my_wallets/wallet_bak2.dat

    some code parts based on:
        https://github.com/gavinandresen/bitcointools/

    :copyleft: 2013 by Jens Diemer, see AUTHORS for more details.
    :license: GNU GPL v3 or above, see LICENSE for more details.
"""

import os
import sys
import bsddb
import datetime
import tempfile
import struct
import mmap


LINK_URLS = (
    ("blockchain.info", "http://blockchain.info/address/%s"),
    ("blockexplorer.com", "http://blockexplorer.com/address/%s"),
)

OWN_FILENAME = os.path.basename(__file__)


# -----------------------------------------------------------------------------
# from https://github.com/gavinandresen/bitcointools/blob/master/BCDataStream.py
#
# Workalike python implementation of Bitcoin's CDataStream class.
#

class SerializationError(Exception):
    """ Thrown when there's a problem deserializing or serializing """

class BCDataStream(object):
    def __init__(self):
        self.input = None
        self.read_cursor = 0

    def clear(self):
        self.input = None
        self.read_cursor = 0

    def write(self, bytes): # Initialize with string of bytes
        if self.input is None:
            self.input = bytes
        else:
            self.input += bytes

    def map_file(self, file, start): # Initialize with bytes from file
        self.input = mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ)
        self.read_cursor = start
    def seek_file(self, position):
        self.read_cursor = position
    def close_file(self):
        self.input.close()

    def read_string(self):
        # Strings are encoded depending on length:
        # 0 to 252 :    1-byte-length followed by bytes (if any)
        # 253 to 65,535 : byte'253' 2-byte-length followed by bytes
        # 65,536 to 4,294,967,295 : byte '254' 4-byte-length followed by bytes
        # ... and the Bitcoin client is coded to understand:
        # greater than 4,294,967,295 : byte '255' 8-byte-length followed by bytes of string
        # ... but I don't think it actually handles any strings that big.
        if self.input is None:
            raise SerializationError("call write(bytes) before trying to deserialize")

        try:
            length = self.read_compact_size()
        except IndexError:
            raise SerializationError("attempt to read past end of buffer")

        return self.read_bytes(length)

    def write_string(self, string):
        # Length-encoded as with read-string
        self.write_compact_size(len(string))
        self.write(string)

    def read_bytes(self, length):
        try:
            result = self.input[self.read_cursor:self.read_cursor + length]
            self.read_cursor += length
            return result
        except IndexError:
            raise SerializationError("attempt to read past end of buffer")

        return ''

    def read_boolean(self): return self.read_bytes(1)[0] != chr(0)
    def read_int16(self): return self._read_num('<h')
    def read_uint16(self): return self._read_num('<H')
    def read_int32(self): return self._read_num('<i')
    def read_uint32(self): return self._read_num('<I')
    def read_int64(self): return self._read_num('<q')
    def read_uint64(self): return self._read_num('<Q')

    def write_boolean(self, val): return self.write(chr(1) if val else chr(0))
    def write_int16(self, val): return self._write_num('<h', val)
    def write_uint16(self, val): return self._write_num('<H', val)
    def write_int32(self, val): return self._write_num('<i', val)
    def write_uint32(self, val): return self._write_num('<I', val)
    def write_int64(self, val): return self._write_num('<q', val)
    def write_uint64(self, val): return self._write_num('<Q', val)

    def read_compact_size(self):
        size = ord(self.input[self.read_cursor])
        self.read_cursor += 1
        if size == 253:
            size = self._read_num('<H')
        elif size == 254:
            size = self._read_num('<I')
        elif size == 255:
            size = self._read_num('<Q')
        return size

    def write_compact_size(self, size):
        if size < 0:
            raise SerializationError("attempt to write size < 0")
        elif size < 253:
            self.write(chr(size))
        elif size < 2 ** 16:
            self.write('\xfd')
            self._write_num('<H', size)
        elif size < 2 ** 32:
            self.write('\xfe')
            self._write_num('<I', size)
        elif size < 2 ** 64:
            self.write('\xff')
            self._write_num('<Q', size)

    def _read_num(self, format):
        (i,) = struct.unpack_from(format, self.input, self.read_cursor)
        self.read_cursor += struct.calcsize(format)
        return i

    def _write_num(self, format, num):
        s = struct.pack(format, num)
        self.write(s)


#------------------------------------------------------------------------------


def get_temp_copy(wallet_filepath):
    print "Make temp file from %r..." % wallet_filepath
    with open(wallet_filepath, "rb") as f:
        temp_wallet = tempfile.NamedTemporaryFile(prefix=OWN_FILENAME)
        temp_wallet.write(f.read())
    print "temp tile %r created" % temp_wallet.name
    return temp_wallet


def get_wallet_db(temp_wallet):
    dir, filename = os.path.split(temp_wallet.name)

    print "open bsddb at directory %r..." % dir
    db_env = bsddb.db.DBEnv()
    db_env.open(dir,
        # bsddb.db.DB_INIT_LOCK
        (bsddb.db.DB_CREATE | bsddb.db.DB_INIT_LOCK | bsddb.db.DB_INIT_LOG | bsddb.db.DB_INIT_MPOOL | bsddb.db.DB_INIT_TXN | bsddb.db.DB_THREAD | bsddb.db.DB_RECOVER)
    )

    db = bsddb.db.DB(db_env)

    print "open file %r..." % filename
    db.open(filename, "main", bsddb.db.DB_BTREE, bsddb.db.DB_RDONLY)
    return db


def extract_addresses(db):
    kds = BCDataStream()
    vds = BCDataStream()

    addr_info = []

    for key, value in db.items():
        kds.clear()
        kds.write(key)
        item_type = kds.read_string()

#         print item_type, repr(key), repr(value)

        vds.clear()
        vds.write(value)

        if item_type == "name":
            hash = kds.read_string()
            name = vds.read_string()
            print hash, name
            addr_info.append(
                (hash, name)
            )
    return addr_info


def get_addr_info(wallet_filepath):
    with get_temp_copy(wallet_filepath) as temp_wallet:
        db = get_wallet_db(temp_wallet)
        try:
            addr_info = extract_addresses(db)
        finally:
            print
            print "Close db %r" % temp_wallet.name
            print
            db.close()

#         print addr_info
    return addr_info


def create_html_info(wallet_filepath):
    addr_info = get_addr_info(wallet_filepath)

    html_filepath = "%s.html" % os.path.splitext(wallet_filepath)[0]
    print
    print "Create %r..." % html_filepath

    with open(html_filepath, "w") as f:
        f.write("<!DOCTYPE html><html><head>")
        f.write("<title>%s</title>" % os.path.basename(wallet_filepath))
        f.write("</head><body>")
        f.write("<h1>%s</h1>" % wallet_filepath)
        f.write("<table>")

        for hash, name in addr_info:
            f.write("<tr>")
            f.write("<td>%s</td>" % hash)

            urls = []
            for txt, url in LINK_URLS:
                url = url % hash
                urls.append('<a href="%s">%s</a>' % (url, txt))

            f.write("<td>%s</td>" % "&nbsp;|&nbsp;".join(urls))
            f.write("<td>%s</td>" % name)
            f.write("</tr>")
        f.write("</table>")
        f.write("<address>Created with <stroing>%s</strong> at %s</address>" % (
            OWN_FILENAME, datetime.datetime.now().strftime("%c")
        ))
        f.write("</body></html>")


if __name__ == "__main__":
    count = 0
    for arg in sys.argv[1:]:
        if os.path.isfile(arg):
            count += 1
            print "_"*79
            print arg
            print
            create_html_info(arg)
            print "\n"*2
        else:
            print "Ignore %r (not a file)"

    if count == 0:
        print "Add 'wallet.dat' files as argument!"
    else:
        print "Process %i wallet files." % count

    print " -- END -- "
