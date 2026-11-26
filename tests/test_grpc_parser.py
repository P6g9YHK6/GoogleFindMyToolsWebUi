import struct

import pytest

from SpotApi.grpc_parser import GrpcParser


def test_construct_grpc_frames_an_uncompressed_message():
    assert GrpcParser.construct_grpc(b"abc") == b"\x00\x00\x00\x00\x03abc"


def test_round_trip():
    payload = bytes(range(256)) * 3
    assert GrpcParser.extract_grpc_payload(GrpcParser.construct_grpc(payload)) == payload


def test_empty_message_round_trips():
    assert GrpcParser.extract_grpc_payload(GrpcParser.construct_grpc(b"")) == b""


def test_extract_ignores_whatever_follows_the_first_message():
    # Spot responses can carry more than the one frame we want.
    body = GrpcParser.construct_grpc(b"first") + GrpcParser.construct_grpc(b"second")
    assert GrpcParser.extract_grpc_payload(body) == b"first"


@pytest.mark.parametrize("body", [b"", b"\x00\x00\x00\x00"], ids=["empty", "short header"])
def test_extract_rejects_a_truncated_header(body):
    with pytest.raises(ValueError, match="Invalid GRPC payload"):
        GrpcParser.extract_grpc_payload(body)


def test_extract_rejects_a_body_shorter_than_its_declared_length():
    body = b"\x00" + struct.pack(">I", 10) + b"short"
    with pytest.raises(ValueError, match="length"):
        GrpcParser.extract_grpc_payload(body)
