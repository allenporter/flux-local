import pytest
from flux_local.store import InMemoryStore, Status, StatusInfo
from flux_local.store.artifact import Artifact
from flux_local.manifest import NamedResource, BaseManifest
from dataclasses import dataclass


@dataclass
class DummyManifest(BaseManifest):
    kind: str
    namespace: str
    name: str
    value: int


@dataclass
class DummyArtifact(Artifact):
    path: str
    revision: str


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


def test_add_and_get_object(store: InMemoryStore) -> None:
    """Test adding and retrieving a manifest object."""
    obj = DummyManifest(kind="TestKind", namespace="ns", name="foo", value=42)
    rid = NamedResource("TestKind", "ns", "foo")
    store.add_object(obj)
    result = store.get_object(rid, DummyManifest)
    assert result == obj

    class NotDummy(BaseManifest):
        pass

    with pytest.raises(
        ValueError, match=r"Object ns/foo is not of type NotDummy \(was DummyManifest\)"
    ):
        store.get_object(rid, NotDummy)


def test_update_and_get_status(store: InMemoryStore) -> None:
    """Test updating and retrieving a status."""
    rid = NamedResource("TestKind", "ns", "bar")
    store.update_status(rid, Status.PENDING)
    assert store.get_status(rid) == StatusInfo(status=Status.PENDING)
    store.update_status(rid, Status.FAILED, error="boom")
    assert store.get_status(rid) == StatusInfo(status=Status.FAILED, error="boom")
    store.update_status(rid, Status.READY)
    assert store.get_status(rid) == StatusInfo(status=Status.READY)


def test_set_and_get_artifact(store: InMemoryStore) -> None:
    """Test setting and retrieving an artifact."""
    rid = NamedResource("TestKind", "ns", "baz")
    artifact = DummyArtifact(path="/tmp/foo", revision="abc123")
    store.set_artifact(rid, artifact)
    result = store.get_artifact(rid, DummyArtifact)
    assert result == artifact

    # Wrong type returns None
    class NotDummyArtifact(Artifact):
        pass

    with pytest.raises(
        ValueError,
        match=r"Artifact ns/baz is not of type NotDummyArtifact \(was DummyArtifact\)",
    ):
        store.get_artifact(rid, NotDummyArtifact)


def test_list_objects(store: InMemoryStore) -> None:
    """Test listing objects."""
    obj1 = DummyManifest(kind="KindA", namespace="ns", name="foo", value=1)
    obj2 = DummyManifest(kind="KindB", namespace="ns", name="bar", value=2)
    obj3 = DummyManifest(kind="KindA", namespace="ns", name="baz", value=3)
    store.add_object(obj1)
    store.add_object(obj2)
    store.add_object(obj3)
    all_objs = store.list_objects()
    assert sorted(all_objs, key=lambda x: x.name) == sorted(  # type: ignore[attr-defined]
        [obj1, obj2, obj3], key=lambda x: x.name
    )
    kind_a_objs = store.list_objects(kind="KindA")
    assert sorted(kind_a_objs, key=lambda x: x.name) == sorted(  # type: ignore[attr-defined]
        [obj1, obj3], key=lambda x: x.name
    )
    kind_b_objs = store.list_objects(kind="KindB")
    assert kind_b_objs == [obj2]
