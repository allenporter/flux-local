import pytest
import asyncio  # Added
from flux_local.store import InMemoryStore, Status, StatusInfo
from flux_local.store.artifact import Artifact
from flux_local.store.store import StoreEvent
from flux_local.manifest import NamedResource, BaseManifest
from flux_local.exceptions import ResourceFailedError  # Added
from dataclasses import dataclass


@dataclass
class DummyManifest(BaseManifest):
    kind: str
    namespace: str
    name: str
    value: int


@dataclass(frozen=True, kw_only=True)
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
        match=r"Artifact/get ns/baz is not of type NotDummyArtifact \(was DummyArtifact\)",
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


def test_object_added_listener(store: InMemoryStore) -> None:
    events = []

    def on_added(resource_id: NamedResource, obj: DummyManifest) -> None:
        events.append((resource_id, obj))

    remove = store.add_listener(StoreEvent.OBJECT_ADDED, on_added)
    obj = DummyManifest(kind="TestKind", namespace="ns", name="foo", value=1)
    rid = NamedResource("TestKind", "ns", "foo")
    store.add_object(obj)
    assert events == [(rid, obj)]
    remove()
    store.add_object(
        DummyManifest(kind="TestKind", namespace="ns", name="bar", value=2)
    )
    # Listener should not be called after removal
    assert len(events) == 1


async def test_watch_ready_already_ready(store: InMemoryStore) -> None:
    """Test watch_ready when the resource is already ready."""
    rid = NamedResource("TestKind", "ns", "already_ready")
    expected_status_info = StatusInfo(status=Status.READY)
    store.update_status(rid, Status.READY)

    status_info = await store.watch_ready(rid)
    assert status_info == expected_status_info


async def test_watch_ready_becomes_ready(store: InMemoryStore) -> None:
    """Test watch_ready when a resource transitions to ready."""
    rid = NamedResource("TestKind", "ns", "becomes_ready")
    store.update_status(rid, Status.PENDING)  # Start as pending

    async def _watch() -> StatusInfo:
        return await store.watch_ready(rid)

    watch_task = asyncio.create_task(_watch())

    # Give the watch_task a moment to start waiting
    await asyncio.sleep(0)
    assert not watch_task.done(), "Watch task finished prematurely"

    expected_status_info = StatusInfo(status=Status.READY)
    store.update_status(rid, Status.READY)

    status_info = await watch_task
    assert status_info == expected_status_info


async def test_watch_ready_resource_fails(store: InMemoryStore) -> None:
    """Test watch_ready when a resource transitions to failed."""
    rid = NamedResource("TestKind", "ns", "becomes_failed")
    store.update_status(rid, Status.PENDING)  # Start as pending

    async def _watch() -> StatusInfo:
        return await store.watch_ready(rid)

    watch_task = asyncio.create_task(_watch())

    # Give the watch_task a moment to start waiting
    await asyncio.sleep(0)
    assert not watch_task.done(), "Watch task finished prematurely"

    error_message = "it borked"
    store.update_status(rid, Status.FAILED, error=error_message)

    with pytest.raises(ResourceFailedError) as excinfo:
        await watch_task
    assert excinfo.value.resource_name == rid.namespaced_name
    assert excinfo.value.message == error_message
    assert str(excinfo.value) == f"Resource ns/becomes_failed failed: {error_message}"


async def test_watch_ready_already_failed(store: InMemoryStore) -> None:
    """Test watch_ready when the resource is already failed."""
    rid = NamedResource("TestKind", "ns", "already_failed")
    error_message = "already kaput"
    store.update_status(rid, Status.FAILED, error=error_message)

    with pytest.raises(ResourceFailedError) as excinfo:
        await store.watch_ready(rid)
    assert excinfo.value.resource_name == rid.namespaced_name
    assert excinfo.value.message == error_message
    assert str(excinfo.value) == f"Resource ns/already_failed failed: {error_message}"


async def test_watch_ready_multiple_waiters(store: InMemoryStore) -> None:
    """Test multiple tasks waiting for the same resource to become ready."""
    rid = NamedResource("TestKind", "ns", "multi_wait_ready")
    store.update_status(rid, Status.PENDING)

    num_waiters = 3
    watch_tasks = []

    async def _watch() -> StatusInfo:
        return await store.watch_ready(rid)

    for _i in range(num_waiters):
        watch_tasks.append(asyncio.create_task(_watch()))

    await asyncio.sleep(0.05)  # Ensure all tasks are waiting
    for task in watch_tasks:
        assert not task.done(), "A watch task finished prematurely"

    expected_status_info = StatusInfo(status=Status.READY)
    store.update_status(rid, Status.READY)

    results = await asyncio.gather(*watch_tasks)
    for status_info_result in results:
        assert status_info_result == expected_status_info


async def test_watch_ready_multiple_waiters_one_fails(store: InMemoryStore) -> None:
    """Test multiple tasks waiting, and the resource fails."""
    rid = NamedResource("TestKind", "ns", "multi_wait_fail")
    store.update_status(rid, Status.PENDING)

    num_waiters = 3
    watch_tasks = []

    async def _watch_expect_fail() -> ResourceFailedError:
        with pytest.raises(ResourceFailedError) as excinfo:
            await store.watch_ready(rid)
        return excinfo.value

    for _i in range(num_waiters):
        watch_tasks.append(asyncio.create_task(_watch_expect_fail()))

    await asyncio.sleep(0.05)  # Ensure all tasks are waiting
    for task in watch_tasks:
        assert not task.done(), "A watch task finished prematurely (fail case)"

    error_message = "multi borked"
    store.update_status(rid, Status.FAILED, error=error_message)

    results = await asyncio.gather(*watch_tasks)
    for exc_value in results:
        assert exc_value.resource_name == rid.namespaced_name
        assert exc_value.message == error_message


async def test_watch_ready_unrelated_update(store: InMemoryStore) -> None:
    """Test that watch_ready is not affected by unrelated status updates."""
    rid_watched = NamedResource("TestKind", "ns", "watched_resource")
    rid_other = NamedResource("TestKind", "ns", "other_resource")

    store.update_status(rid_watched, Status.PENDING)
    store.update_status(rid_other, Status.PENDING)

    async def _watch() -> StatusInfo:
        return await store.watch_ready(rid_watched)

    watch_task = asyncio.create_task(_watch())

    await asyncio.sleep(0)
    assert not watch_task.done()

    # Update another resource
    store.update_status(rid_other, Status.READY)
    await asyncio.sleep(0)  # Give time for any incorrect wake-up
    assert not watch_task.done(), "Watch task woke up on unrelated update"

    # Now update the watched resource
    expected_status_info = StatusInfo(status=Status.READY)
    store.update_status(rid_watched, Status.READY)

    status_info = await watch_task
    assert status_info == expected_status_info


async def test_watch_added_no_initial_then_add(store: InMemoryStore) -> None:
    """Test watch_added when no initial objects exist, then objects are added."""
    kind_to_watch = "KindA"
    received_items = []

    async def consume_watcher() -> None:
        async for item in store.watch_added(kind_to_watch):
            received_items.append(item)

    watcher_task = asyncio.create_task(consume_watcher())

    # Give watcher a chance to start and confirm no initial items
    await asyncio.sleep(0)
    assert not received_items

    # Add first object
    obj1 = DummyManifest(kind=kind_to_watch, namespace="ns", name="foo1", value=1)
    rid1 = NamedResource(obj1.kind, obj1.namespace, obj1.name)
    store.add_object(obj1)
    await asyncio.sleep(0)  # Allow event to propagate
    assert len(received_items) == 1
    assert received_items[0] == (rid1, obj1)

    # Add second object
    obj2 = DummyManifest(kind=kind_to_watch, namespace="ns", name="foo2", value=2)
    rid2 = NamedResource(obj2.kind, obj2.namespace, obj2.name)
    store.add_object(obj2)
    await asyncio.sleep(0)  # Allow event to propagate
    assert len(received_items) == 2
    assert received_items[1] == (rid2, obj2)

    watcher_task.cancel()
    try:
        await watcher_task
    except asyncio.CancelledError:
        pass


async def test_watch_added_with_initial_then_add(store: InMemoryStore) -> None:
    """Test watch_added when initial objects exist, then more are added."""
    kind_to_watch = "KindA"
    obj1 = DummyManifest(kind=kind_to_watch, namespace="ns", name="foo1", value=1)
    rid1 = NamedResource(obj1.kind, obj1.namespace, obj1.name)
    store.add_object(obj1)

    received_items = []

    async def consume_watcher() -> None:
        async for item in store.watch_added(kind_to_watch):
            received_items.append(item)

    watcher_task = asyncio.create_task(consume_watcher())

    await asyncio.sleep(0)  # Allow initial items to be processed
    assert len(received_items) == 1
    assert received_items[0] == (rid1, obj1)

    # Add second object
    obj2 = DummyManifest(kind=kind_to_watch, namespace="ns", name="foo2", value=2)
    rid2 = NamedResource(obj2.kind, obj2.namespace, obj2.name)
    store.add_object(obj2)
    await asyncio.sleep(0)  # Allow event to propagate
    assert len(received_items) == 2
    assert received_items[1] == (rid2, obj2)

    watcher_task.cancel()
    try:
        await watcher_task
    except asyncio.CancelledError:
        pass


async def test_watch_added_filters_by_kind(store: InMemoryStore) -> None:
    """Test that watch_added correctly filters by kind."""
    kind_a = "KindA"
    kind_b = "KindB"
    received_items_a = []

    async def consume_watcher_a() -> None:
        async for item in store.watch_added(kind_a):
            received_items_a.append(item)

    watcher_a_task = asyncio.create_task(consume_watcher_a())

    # Add KindB object - should not be received by KindA watcher
    obj_b = DummyManifest(kind=kind_b, namespace="ns", name="bar", value=100)
    store.add_object(obj_b)
    await asyncio.sleep(0)
    assert not received_items_a

    # Add KindA object - should be received
    obj_a = DummyManifest(kind=kind_a, namespace="ns", name="foo", value=1)
    rid_a = NamedResource(obj_a.kind, obj_a.namespace, obj_a.name)
    store.add_object(obj_a)
    await asyncio.sleep(0)
    assert len(received_items_a) == 1
    assert received_items_a[0] == (rid_a, obj_a)

    watcher_a_task.cancel()
    try:
        await watcher_a_task
    except asyncio.CancelledError:
        pass


async def test_watch_added_cancellation(store: InMemoryStore) -> None:
    """Test that watch_added can be cancelled and cleans up listeners."""
    kind_to_watch = "KindA"
    received_items = []

    async def consume_watcher() -> None:
        try:
            async for item in store.watch_added(kind_to_watch):
                received_items.append(item)
        except asyncio.CancelledError:
            # Ensure listener is removed even if cancelled mid-iteration
            # (The finally block in watch_added should handle this)
            raise

    watcher_task = asyncio.create_task(consume_watcher())
    await asyncio.sleep(0)  # Let watcher start

    watcher_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await watcher_task

    # Check that listener was removed by trying to add an object
    # and ensuring the cancelled watcher doesn't receive it (implicitly, by not crashing)
    # This is a bit indirect; a more direct way would be to inspect store._listeners if possible,
    # but that's internal. For now, ensure no errors and received_items is empty.
    assert not received_items
    obj1 = DummyManifest(kind=kind_to_watch, namespace="ns", name="foo1", value=1)
    store.add_object(
        obj1
    )  # If listener wasn't removed, this might cause issues with the closed queue
    await asyncio.sleep(0)
    assert not received_items  # Confirm no items were added after cancellation


async def test_watch_added_add_identical_object_no_new_event(
    store: InMemoryStore,
) -> None:
    """Test adding an identical object does not trigger watch_added again after initial yield."""
    kind_to_watch = "KindA"
    obj1 = DummyManifest(kind=kind_to_watch, namespace="ns", name="foo1", value=1)
    rid1 = NamedResource(obj1.kind, obj1.namespace, obj1.name)
    store.add_object(obj1)

    received_items = []

    async def consume_watcher() -> None:
        async for item in store.watch_added(kind_to_watch):
            received_items.append(item)

    watcher_task = asyncio.create_task(consume_watcher())
    await asyncio.sleep(0)  # Process initial item
    assert len(received_items) == 1
    assert received_items[0] == (rid1, obj1)

    # Add the exact same object again
    store.add_object(obj1)
    await asyncio.sleep(0)  # Give time for any potential event
    assert len(received_items) == 1  # Should not have received it again

    watcher_task.cancel()
    try:
        await watcher_task
    except asyncio.CancelledError:
        pass


async def test_watch_added_add_updated_object_fires_event(store: InMemoryStore) -> None:
    """Test adding an object with the same ID but different content fires watch_added."""
    kind_to_watch = "KindA"
    obj1 = DummyManifest(kind=kind_to_watch, namespace="ns", name="foo1", value=1)
    rid1 = NamedResource(obj1.kind, obj1.namespace, obj1.name)
    store.add_object(obj1)

    received_items = []

    async def consume_watcher() -> None:
        async for item in store.watch_added(kind_to_watch):
            received_items.append(item)

    watcher_task = asyncio.create_task(consume_watcher())
    await asyncio.sleep(0)  # Process initial item
    assert len(received_items) == 1
    assert received_items[0] == (rid1, obj1)

    # Add updated object (same ID, different value)
    obj1_updated = DummyManifest(
        kind=kind_to_watch, namespace="ns", name="foo1", value=2
    )
    store.add_object(obj1_updated)
    await asyncio.sleep(0)  # Allow event to propagate
    assert len(received_items) == 2
    assert received_items[1] == (rid1, obj1_updated)  # rid1 is the same

    watcher_task.cancel()
    try:
        await watcher_task
    except asyncio.CancelledError:
        pass


async def test_watch_added_multiple_concurrent_watchers_same_kind(
    store: InMemoryStore,
) -> None:
    """Test multiple concurrent watchers for the same kind."""
    kind_to_watch = "KindA"
    received_items1: list[tuple[NamedResource, BaseManifest]] = []
    received_items2: list[tuple[NamedResource, BaseManifest]] = []

    async def consume_watcher(
        item_list: list[tuple[NamedResource, BaseManifest]],
    ) -> None:
        async for item in store.watch_added(kind_to_watch):
            item_list.append(item)

    watcher_task1 = asyncio.create_task(consume_watcher(received_items1))
    watcher_task2 = asyncio.create_task(consume_watcher(received_items2))

    await asyncio.sleep(0)  # Let watchers start

    obj1 = DummyManifest(kind=kind_to_watch, namespace="ns", name="foo1", value=1)
    rid1 = NamedResource(obj1.kind, obj1.namespace, obj1.name)
    store.add_object(obj1)
    await asyncio.sleep(0)  # Allow event to propagate

    assert len(received_items1) == 1
    assert received_items1[0] == (rid1, obj1)
    assert len(received_items2) == 1
    assert received_items2[0] == (rid1, obj1)

    obj2 = DummyManifest(kind=kind_to_watch, namespace="ns", name="foo2", value=2)
    rid2 = NamedResource(obj2.kind, obj2.namespace, obj2.name)
    store.add_object(obj2)
    await asyncio.sleep(0)  # Allow event to propagate

    assert len(received_items1) == 2
    assert received_items1[1] == (rid2, obj2)
    assert len(received_items2) == 2
    assert received_items2[1] == (rid2, obj2)

    watcher_task1.cancel()
    watcher_task2.cancel()
    try:
        await watcher_task1
    except asyncio.CancelledError:
        pass
    try:
        await watcher_task2
    except asyncio.CancelledError:
        pass
