"""Pruebas del almacén de alertas y del registro de clientes."""

from __future__ import annotations

from incidentes.server.models import AlertIn, AlertRecord
from incidentes.server.registry import ClientRegistry
from incidentes.server.store import AlertStore


def make_record() -> AlertRecord:
    return AlertRecord(
        ticket="",
        status="registrada",
        priority="ALTA",
        eta_min=5,
        units=["patrulla"],
        received_at="2026-10-21T10:00:00+00:00",
        alert=AlertIn(client_id="camara-01", type="PELEA"),
    )


def test_store_assigns_sequential_tickets() -> None:
    store = AlertStore()
    first = store.add(make_record())
    second = store.add(make_record())
    assert first.ticket.startswith("ALT-")
    assert first.ticket.endswith("-0001")
    assert second.ticket.endswith("-0002")
    assert store.count() == 2


def test_store_lists_newest_first() -> None:
    store = AlertStore()
    first = store.add(make_record())
    second = store.add(make_record())
    tickets = [record["ticket"] for record in store.list()]
    assert tickets == [second.ticket, first.ticket]


def test_store_updates_status() -> None:
    store = AlertStore()
    ticket = store.add(make_record()).ticket
    updated = store.update_status(ticket, "atendida")
    assert updated is not None
    assert updated["status"] == "atendida"
    assert store.update_status("ALT-inexistente", "x") is None


def test_registry_registers_and_refreshes_clients() -> None:
    registry = ClientRegistry(alive_seconds=30)
    first = registry.register("camara-01", address="Centro", ip="10.0.0.1")
    again = registry.register("camara-01", address="Centro", ip="10.0.0.2")
    assert first["nuevo"] is True
    assert again["nuevo"] is False
    assert registry.count() == 1
    assert registry.heartbeat("camara-01") is not None
    assert registry.heartbeat("camara-99") is None


def test_registry_marks_stale_clients_offline() -> None:
    registry = ClientRegistry(alive_seconds=-1)
    registry.register("camara-01")
    assert registry.list(only_alive=True) == []
    assert len(registry.list(only_alive=False)) == 1


def test_registry_removes_clients() -> None:
    registry = ClientRegistry()
    registry.register("camara-01")
    assert registry.remove("camara-01") is True
    assert registry.remove("camara-01") is False
