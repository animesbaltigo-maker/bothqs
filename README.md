<<<<<<< HEAD
# HQ Baltigo

Bot de HQ para Telegram com busca, detalhes, capitulos, leitura por pagina, continuar leitura, favoritas, historico, editoras, mais vistas e atualizacoes usando o HQ Now como fonte principal.

## Recursos

- Busca por nome
- Painel detalhado da HQ
- Lista paginada de capitulos
- Leitura por pagina com retomada exata
- Leitura rapida no Telegraph
- Geracao de PDF
- Favoritas e historico local em SQLite
- Editoras, populares e atualizacoes
- Broadcast, metricas e referrals

## Instalar

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
copy .env.example .env
```

Preencha `BOT_TOKEN` e, se quiser, os demais campos no `.env`.

## Rodar

```bash
python bot.py
```

## Observacoes

- A fonte principal usa a API GraphQL publica usada pelo proprio frontend do HQ Now.
- O projeto usa cache em memoria e SQLite para reduzir requests repetidos.
- A leitura por pagina salva `chapter_id`, `page_number` e a URL exata do leitor para retomada.

=======
# bothqs
>>>>>>> 668a7e96ba394bbededf795bea7100e16d3964aa
