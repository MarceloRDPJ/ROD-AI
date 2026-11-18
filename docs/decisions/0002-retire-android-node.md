# ADR 0002 — Aposentar o nó Android

Status: aceito em 2026-11-18.

O aparelho Android deixou de funcionar e não será substituído. O aplicativo, protocolo de heartbeat, jobs remotos, artefatos e integrações de contas foram removidos porque anunciavam capacidades indisponíveis e aumentavam a superfície de manutenção.

Consequência: speedtest e diagnóstico são executados no Pi; consultas de concessionárias não são oferecidas. O histórico permanece no Git, não no runtime nem na documentação operacional.
