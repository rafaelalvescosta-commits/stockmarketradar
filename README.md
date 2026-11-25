# stockmarketradar

Radar de oportunidades em ações dos EUA. Depois do fechamento de Nova York, o código escaneia o mercado (técnica, volume, níveis) e o Claude pesquisa as notícias de cada sinal, classifica a causa da queda e escreve a análise do dia.

- **Página**: o artefato "Radar do Pregão" no Claude, com o último pregão, os últimos 15 pregões e o placar dos sinais.
- **Rotina**: tarefa agendada do Claude, de segunda a sexta às 19:45 (Brasília), seguindo `.claude/skills/radar-diario/SKILL.md`. O resumo chega como notificação no celular.
- **Scanner**: Dip Scanner, quedas fortes com causa pontual para swing de 3 a 10 dias. Detalhes em [docs/dip-scanner.md](docs/dip-scanner.md).

```bash
pip install -r requirements-dev.txt
python -m radar.scanner scan --out out/day.json
python -m pytest -q
```

Ferramenta de apoio à decisão. Não é recomendação de investimento.
