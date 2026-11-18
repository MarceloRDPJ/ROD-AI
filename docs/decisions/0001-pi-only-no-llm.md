# ADR 0001 — Runtime Pi-only e sem LLM

Status: aceito.

O único computador do produto é o Raspberry Pi 3B. O atendimento usa regras, fuzzy matching conservador e fontes reais. Modelos locais competiriam com Telegram, AdGuard e CasaOS pela memória; serviços externos adicionariam custo, latência e respostas não verificáveis.

Consequência: o ROD responde rapidamente dentro de um conjunto explícito de skills e esclarece pedidos fora dele. Uma futura IA exige decisão nova, benchmark no hardware e comportamento opcional que não prejudique o caminho operacional.
