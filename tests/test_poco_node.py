import json

import pytest

from jarvis.services.poco_node import PocoAuthenticationError, PocoNodeService


def test_signed_request_and_expiration(tmp_path):
    now = [1_700_000_000]
    service = PocoNodeService(tmp_path / "poco.json", "secret", clock=lambda: now[0])
    body = b'{"node_id":"poco"}'
    timestamp = str(now[0])
    signature = service.signature("secret", timestamp, "POST", "/api/poco/heartbeat", body)
    service.authenticate(timestamp, signature, "POST", "/api/poco/heartbeat", body)

    now[0] += 121
    with pytest.raises(PocoAuthenticationError):
        service.authenticate(timestamp, signature, "POST", "/api/poco/heartbeat", body)


def test_queue_is_persistent_and_idempotent(tmp_path):
    path = tmp_path / "poco.json"
    service = PocoNodeService(path, "secret")
    created = service.enqueue("device_status", {"detail": "basic"})
    accepted = service.next_job()
    assert accepted.job_id == created.job_id
    assert accepted.status == "accepted"

    running = service.update_job(created.job_id, "running")
    assert running.status == "running"
    completed = service.update_job(created.job_id, "completed", {"battery_level": 80})
    assert completed.result == {"battery_level": 80}
    assert service.update_job(created.job_id, "failed", error="late").status == "completed"

    restored = PocoNodeService(path, "secret")
    assert restored.status()["queued_jobs"] == 0
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["jobs"][0]["status"] == "completed"


def test_rejects_arbitrary_actions(tmp_path):
    service = PocoNodeService(tmp_path / "poco.json", "secret")
    with pytest.raises(ValueError):
        service.enqueue("shell", {"command": "anything"})


def test_heartbeat_is_sanitized_and_becomes_stale(tmp_path):
    now = [1000]
    service = PocoNodeService(tmp_path / "poco.json", "secret", heartbeat_stale_seconds=10, clock=lambda: now[0])
    service.record_heartbeat(
        {
            "node_id": "poco-x3",
            "battery_level": 500,
            "battery_temperature_c": 32.5,
            "thermal_status": "none",
            "wifi_connected": True,
            "agent_version": "0.1",
            "ignored_secret": "must-not-persist",
        }
    )
    assert service.status()["online"] is True
    persisted = (tmp_path / "poco.json").read_text(encoding="utf-8")
    assert "ignored_secret" not in persisted
    assert service.status()["heartbeat"]["battery_level"] == 100
    now[0] += 11
    assert service.status()["online"] is False


def test_abandoned_running_job_expires_and_does_not_block_queue(tmp_path):
    now = [1000.0]
    service = PocoNodeService(tmp_path / "poco.json", shared_secret="x", clock=lambda: now[0])
    first = service.enqueue("device_status", ttl_seconds=10)
    service.next_job()
    service.update_job(first.job_id, "running")
    second = service.enqueue("network_check", ttl_seconds=60)
    now[0] += 11

    selected = service.next_job()

    assert service._jobs[first.job_id].status == "expired"
    assert selected.job_id == second.job_id


def test_get_job_returns_detached_snapshot(tmp_path):
    service = PocoNodeService(tmp_path / "poco.json", shared_secret="x")
    created = service.enqueue("network_check")

    snapshot = service.get_job(created.job_id)
    snapshot.status = "completed"

    assert service.get_job(created.job_id).status == "queued"


def test_abandoned_lease_returns_to_queue(tmp_path):
    """A job handed out but never confirmed must not sit idle until the TTL.

    If the Wi-Fi drops exactly at dispatch the node never sees the job. Before the
    lease existed the user waited the whole timeout for work that never started.
    """
    now = [1000.0]
    service = PocoNodeService(
        tmp_path / "poco.json", shared_secret="x", lease_seconds=60, clock=lambda: now[0]
    )
    created = service.enqueue("refresh_saneago_bills", ttl_seconds=600)

    first = service.next_job()
    assert first.job_id == created.job_id and first.attempts == 1

    now[0] += 61
    redelivered = service.next_job()

    assert redelivered.job_id == created.job_id
    assert redelivered.attempts == 2


def test_lease_requeue_gives_up_instead_of_looping_forever(tmp_path):
    now = [1000.0]
    service = PocoNodeService(
        tmp_path / "poco.json",
        shared_secret="x",
        lease_seconds=10,
        max_attempts=2,
        clock=lambda: now[0],
    )
    created = service.enqueue("refresh_equatorial_bills", ttl_seconds=600)

    for _ in range(2):
        service.next_job()
        now[0] += 11

    assert service.next_job() is None
    failed = service.get_job(created.job_id)
    assert failed.status == "failed"
    assert "não confirmou" in failed.error


def test_requeued_job_still_accepts_a_late_result_from_the_outbox(tmp_path):
    """The node keeps results in a durable outbox and may deliver them after a requeue.

    Answering 4xx here would make the node treat a real reading as permanently
    rejected and drop it, so the query would fail even though the phone succeeded.
    """
    now = [1000.0]
    service = PocoNodeService(
        tmp_path / "poco.json", shared_secret="x", lease_seconds=10, clock=lambda: now[0]
    )
    created = service.enqueue("refresh_saneago_bills", ttl_seconds=600)
    service.next_job()
    now[0] += 11
    service.next_job()  # sweep puts it back and hands it out again

    completed = service.update_job(created.job_id, "completed", {"amount": "123,45"})

    assert completed.status == "completed"
    assert completed.result == {"amount": "123,45"}


def test_heartbeat_carries_busy_and_pending_results(tmp_path):
    service = PocoNodeService(tmp_path / "poco.json", "secret")
    service.record_heartbeat({"node_id": "poco", "busy": True, "pending_results": 3})

    heartbeat = service.status()["heartbeat"]

    assert heartbeat["busy"] is True
    assert heartbeat["pending_results"] == 3


def test_heartbeat_keeps_only_known_property_names(tmp_path):
    """O menu recebe nomes lógicos, nunca texto arbitrário ou identificadores."""
    service = PocoNodeService(tmp_path / "poco.json", "secret")
    service.record_heartbeat({
        "water_properties": ["casa", "kitnet_01", "casa", "20425805"],
        "energy_properties": ["restaurante", "../../segredo", "sala_comercial"],
    })

    heartbeat = service.status()["heartbeat"]
    assert heartbeat["water_properties"] == ["casa", "kitnet_01"]
    assert heartbeat["energy_properties"] == ["restaurante", "sala_comercial"]
    assert "20425805" not in (tmp_path / "poco.json").read_text(encoding="utf-8")


def test_lease_requeue_never_resurrects_a_terminal_job(tmp_path):
    """Negative guard: a finished job must never be dispatched a second time."""
    now = [1000.0]
    service = PocoNodeService(
        tmp_path / "poco.json", shared_secret="x", lease_seconds=10, clock=lambda: now[0]
    )
    created = service.enqueue("refresh_saneago_bills", ttl_seconds=600)
    service.next_job()
    service.update_job(created.job_id, "completed", {"amount": "10,00"})

    now[0] += 300

    assert service.next_job() is None
    assert service.get_job(created.job_id).status == "completed"


def test_payment_data_does_not_linger_in_the_queue_file(tmp_path):
    """Valor, código de barras e PIX não podem ficar guardados indefinidamente.

    O arquivo da fila fica ao lado de um dashboard sem autenticação documentada, e
    o consumidor legítimo lê o resultado em segundos. Faturas acumulavam ali desde
    o primeiro dia de uso, em texto claro.
    """
    now = [1000.0]
    service = PocoNodeService(
        tmp_path / "poco.json",
        shared_secret="x",
        result_grace_seconds=600,
        clock=lambda: now[0],
    )
    created = service.enqueue("refresh_equatorial_bills", ttl_seconds=600)
    service.next_job()
    service.update_job(created.job_id, "completed", {"amount": "10,00", "barcode": "8" * 48})
    assert service.get_job(created.job_id).result is not None

    now[0] += 601
    service.next_job()  # a varredura roda junto com a entrega do próximo job

    assert service.get_job(created.job_id).result is None
    assert service.get_job(created.job_id).status == "completed"
    assert "barcode" not in (tmp_path / "poco.json").read_text(encoding="utf-8")


def test_old_terminal_jobs_leave_the_queue_entirely(tmp_path):
    now = [1000.0]
    service = PocoNodeService(
        tmp_path / "poco.json", shared_secret="x", retention_seconds=3600, clock=lambda: now[0]
    )
    created = service.enqueue("device_status", ttl_seconds=60)
    service.next_job()
    service.update_job(created.job_id, "completed", {"battery_level": 80})

    now[0] += 3601
    service.next_job()

    assert service.get_job(created.job_id) is None


def test_a_result_arriving_after_the_ttl_is_not_thrown_away(tmp_path):
    """Uma leitura real custa minutos de automação; recusá-la a descartava.

    O nó guarda o resultado na fila local e pode só conseguir entregá-lo depois do
    TTL. Responder recusa fazia o agente tratar como rejeição definitiva e apagar
    a leitura da própria fila.
    """
    now = [1000.0]
    service = PocoNodeService(tmp_path / "poco.json", shared_secret="x", clock=lambda: now[0])
    created = service.enqueue("refresh_equatorial_bills", ttl_seconds=30)
    service.next_job()
    now[0] += 31
    service.next_job()  # varredura expira o job
    assert service.get_job(created.job_id).status == "expired"

    recovered = service.update_job(created.job_id, "completed", {"amount": "10,00"})

    assert recovered.status == "completed"
    assert recovered.result == {"amount": "10,00"}


def test_the_heartbeat_keeps_the_version_code_that_identifies_the_binary(tmp_path):
    """Nome da versao nao distingue dois builds locais; o codigo distingue.

    A lista de campos aceitos e de permissao explicita, entao um campo novo que
    o agente passe a enviar e descartado em silencio — custo no telefone e
    nenhum ganho aqui. Aconteceu exatamente isso: o agente ja mandava o codigo
    da versao e o Pi respondia nulo, deixando o inventario sem saber qual APK
    estava no telefone.
    """
    service = PocoNodeService(tmp_path / "poco.json", "secret")

    saved = service.record_heartbeat({"node_id": "poco", "agent_version": "1.0.2",
                                      "agent_version_code": 34})

    assert saved["agent_version_code"] == 34
    assert saved["agent_version"] == "1.0.2"


def test_a_nonsense_version_code_becomes_zero_instead_of_reaching_disk(tmp_path):
    """O campo vem do telefone: e entrada, nao verdade."""
    service = PocoNodeService(tmp_path / "poco.json", "secret")

    saved = service.record_heartbeat({"node_id": "poco", "agent_version_code": "trinta e quatro"})

    assert saved["agent_version_code"] == 0
