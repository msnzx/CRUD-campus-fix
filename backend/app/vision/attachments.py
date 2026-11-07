"""Private, bounded storage reads. Never follow a URL from an attachment row.

The dependency-free decoder accepts static 8-bit PNG only, validates CRCs and
bounded decompression, and strips all ancillary metadata. Other formats fail
closed pending an approved decoder dependency; bucket acceptance is not proof
that a file is safe for this stage.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import re
import struct
import time
from urllib.parse import urlsplit
import zlib

import httpx

from .contract import VisionFailure,sha

MAX_RAW=7_500_000
MAX_TOTAL=12_000_000
MAX_PIXELS=8_000_000
BUCKET='ticket-attachments'
PATH=re.compile(r'([1-9][0-9]*)/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.([A-Za-z0-9]{1,10})')


@dataclass(frozen=True)
class Image:
    attachment_id: int
    data: bytes
    raw_sha256: str
    width: int
    height: int

    @property
    def encoded(self):
        return ('image/png',base64.b64encode(self.data).decode('ascii'))

    @property
    def provenance(self):
        return {'attachment_id':self.attachment_id,'raw_sha256':self.raw_sha256,
                'sanitized_sha256':sha(self.data),'width':self.width,'height':self.height,
                'media_type':'image/png','sanitizer_version':'static-png-1'}


def validate_record(row: dict,ticket_id: int) -> None:
    path=row.get('file_url')
    match=PATH.fullmatch(path) if isinstance(path,str) else None
    if not match or match[1]!=str(ticket_id) or row.get('ticket_id')!=ticket_id:
        raise VisionFailure('ATTACHMENT_PATH')
    if type(row.get('attachment_id')) is not int or row['attachment_id']<=0:
        raise VisionFailure('ATTACHMENT_METADATA')
    if row.get('file_type')!='image/png' or match[3].lower()!='png':
        raise VisionFailure('UNSUPPORTED_FORMAT')
    if type(row.get('file_size_bytes')) is not int or not 0<row['file_size_bytes']<=MAX_RAW:
        raise VisionFailure('ATTACHMENT_SIZE')
    if not row.get('object_id') or not row.get('uploaded_by') or row.get('owner_id')!=str(row['uploaded_by']):
        raise VisionFailure('ATTACHMENT_OWNERSHIP')


def _chunk(kind,data):
    return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)


def sanitize_png(raw: bytes) -> tuple[bytes,int,int]:
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_RAW: raise VisionFailure('ATTACHMENT_SIZE')
    if raw[:8]!=b'\x89PNG\r\n\x1a\n': raise VisionFailure('MIME_MISMATCH')
    pos=8;chunks=[];header=None;compressed=[];ended=False;after_idat=False
    try:
        while pos<len(raw):
            if len(chunks)>256 or pos+12>len(raw): raise ValueError()
            size=struct.unpack('>I',raw[pos:pos+4])[0]
            kind=raw[pos+4:pos+8]
            if not re.fullmatch(b'[A-Za-z]{4}',kind) or pos+12+size>len(raw): raise ValueError()
            data=raw[pos+8:pos+8+size]
            crc=struct.unpack('>I',raw[pos+8+size:pos+12+size])[0]
            if crc!=(zlib.crc32(kind+data)&0xffffffff): raise ValueError()
            pos+=size+12
            if not chunks and kind!=b'IHDR': raise ValueError()
            chunks.append(kind)
            if kind==b'IHDR':
                if header is not None or size!=13: raise ValueError()
                header=data
                width,height,depth,color,compression,filtering,interlace=struct.unpack('>IIBBBBB',header)
                if not (0<width<=8000 and 0<height<=8000 and width*height<=MAX_PIXELS):
                    raise VisionFailure('IMAGE_DIMENSIONS')
                if depth!=8 or color not in (0,2,4,6) or (compression,filtering,interlace)!=(0,0,0):
                    raise VisionFailure('UNSUPPORTED_PNG_ENCODING')
            elif kind==b'IDAT':
                if after_idat: raise ValueError()
                compressed.append(data)
            elif kind==b'IEND':
                if size or not compressed or pos!=len(raw): raise ValueError()
                ended=True;break
            elif kind in (b'acTL',b'fcTL',b'fdAT'):
                raise VisionFailure('ANIMATED_IMAGE')
            elif not kind[0]&32: # unknown critical chunks, including palettes
                raise VisionFailure('UNSUPPORTED_PNG_ENCODING')
            elif compressed: after_idat=True
        if not ended or header is None: raise ValueError()
        channels={0:1,2:3,4:2,6:4}[color]
        stride=width*channels+1;expected=stride*height
        decoder=zlib.decompressobj()
        decoded=decoder.decompress(b''.join(compressed),expected+1)
        if len(decoded)!=expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError()
        if any(decoded[i]>4 for i in range(0,expected,stride)): raise ValueError()
        # Re-encode only the validated pixel stream; OCR/EXIF/text chunks omitted.
        sanitized=raw[:8]+_chunk(b'IHDR',header)+_chunk(b'IDAT',zlib.compress(decoded))+_chunk(b'IEND',b'')
        if len(sanitized)>MAX_RAW: raise VisionFailure('ATTACHMENT_SIZE')
        return sanitized,width,height
    except (ValueError,struct.error,zlib.error):
        raise VisionFailure('IMAGE_INVALID') from None


class StorageLoader:
    def __init__(self,url: str,key: str,*,transport=None):
        parts=urlsplit(url)
        if not isinstance(key,str) or not key.strip(): raise VisionFailure('STORAGE_CONFIG')
        local=parts.hostname in ('localhost','127.0.0.1','::1')
        if (parts.scheme!='https' and not (parts.scheme=='http' and local)) or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
            raise VisionFailure('STORAGE_CONFIG')
        self.url=url.rstrip('/')
        self.key=key
        self.transport=transport

    def load(self,row: dict,ticket_id: int) -> Image:
        validate_record(row,ticket_id)
        # No signed URLs, redirects, environment proxies, unbounded SDK download,
        # or reporter-controlled host/path fragments. Credentials stay on origin.
        try:
            deadline=time.monotonic()+15
            with httpx.Client(timeout=httpx.Timeout(15,connect=5),follow_redirects=False,
                              trust_env=False,transport=self.transport) as client:
                with client.stream('GET',self.url+'/storage/v1/object/authenticated/'+BUCKET+'/'+row['file_url'],
                    headers={'apikey':self.key,'Authorization':'Bearer '+self.key,'Accept-Encoding':'identity'}) as response:
                    if response.status_code in (404,429) or response.status_code>=500:
                        raise VisionFailure('STORAGE_UNAVAILABLE',True)
                    if response.status_code!=200: raise VisionFailure('STORAGE_REJECTED')
                    if response.headers.get('content-encoding','identity').lower()!='identity':
                        raise VisionFailure('STORAGE_ENCODING')
                    if response.headers.get('content-type','').split(';')[0].strip().lower()!='image/png':
                        raise VisionFailure('MIME_MISMATCH')
                    length=response.headers.get('content-length')
                    if length is not None and (not length.isdecimal() or int(length)!=row['file_size_bytes']):
                        raise VisionFailure('ATTACHMENT_SIZE')
                    body=bytearray();total=0
                    for piece in response.iter_raw():
                        if time.monotonic()>deadline: raise VisionFailure('STORAGE_UNAVAILABLE',True)
                        total+=len(piece)
                        if total>min(MAX_RAW,row['file_size_bytes']): raise VisionFailure('ATTACHMENT_SIZE')
                        body.extend(piece)
            raw=bytes(body)
        except httpx.HTTPError:
            raise VisionFailure('STORAGE_UNAVAILABLE',True) from None
        if len(raw)!=row['file_size_bytes']: raise VisionFailure('ATTACHMENT_SIZE')
        data,width,height=sanitize_png(raw)
        return Image(row['attachment_id'],data,sha(raw),width,height)


def load_all(loader,rows: list[dict],ticket_id: int) -> list[Image]:
    if not rows or len(rows)>4: raise VisionFailure('ATTACHMENT_COUNT')
    for row in rows: validate_record(row,ticket_id)
    if len({r['attachment_id'] for r in rows})!=len(rows) or sum(r['file_size_bytes'] for r in rows)>MAX_TOTAL:
        raise VisionFailure('ATTACHMENT_SIZE')
    # All or review: do not silently discard a potentially safety-relevant image.
    images=[loader.load(row,ticket_id) for row in rows]
    if sum(len(image.data) for image in images)>MAX_TOTAL: raise VisionFailure('ATTACHMENT_SIZE')
    return images
