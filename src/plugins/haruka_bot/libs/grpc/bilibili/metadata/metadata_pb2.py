# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: bilibili/metadata/metadata.proto
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import descriptor_pool as _descriptor_pool
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database

# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()


DESCRIPTOR = _descriptor_pool.Default().AddSerializedFile(
    b'\n bilibili/metadata/metadata.proto\x12\x11\x62ilibili.metadata"\x81\x01\n\x08Metadata\x12\x12\n\naccess_key\x18\x01 \x01(\t\x12\x10\n\x08mobi_app\x18\x02 \x01(\t\x12\x0e\n\x06\x64\x65vice\x18\x03 \x01(\t\x12\r\n\x05\x62uild\x18\x04 \x01(\x05\x12\x0f\n\x07\x63hannel\x18\x05 \x01(\t\x12\r\n\x05\x62uvid\x18\x06 \x01(\t\x12\x10\n\x08platform\x18\x07 \x01(\tb\x06proto3'
)


_METADATA = DESCRIPTOR.message_types_by_name["Metadata"]
Metadata = _reflection.GeneratedProtocolMessageType(
    "Metadata",
    (_message.Message,),
    {
        "DESCRIPTOR": _METADATA,
        "__module__": "bilibili.metadata.metadata_pb2"
        # @@protoc_insertion_point(class_scope:bilibili.metadata.Metadata)
    },
)
_sym_db.RegisterMessage(Metadata)

if _descriptor._USE_C_DESCRIPTORS == False:

    DESCRIPTOR._options = None
    _METADATA._serialized_start = 56
    _METADATA._serialized_end = 185
# @@protoc_insertion_point(module_scope)
