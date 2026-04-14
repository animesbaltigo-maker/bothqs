from __future__ import annotations

import html

from config import BOT_BRAND, BOT_USERNAME


def start_text(first_name: str, *, popular_titles: list[str] | None = None, updated_titles: list[str] | None = None) -> str:
    query_hint = f"@{BOT_USERNAME} batman" if BOT_USERNAME else "/buscar batman"
    popular_titles = [title for title in (popular_titles or []) if title][:2]
    updated_titles = [title for title in (updated_titles or []) if title][:2]

    lines = [
        f"📚 <b>{html.escape(BOT_BRAND)}</b>",
        "",
        f"Oi, {html.escape(first_name)}. Aqui a ideia e simples: achar rapido, abrir limpo e continuar do ponto certo.",
        "",
        "O que voce consegue fazer agora:",
        "• buscar qualquer HQ por nome",
        "• retomar a ultima leitura em 1 toque",
        "• salvar favoritas e historico",
        "• abrir leitura rapida ou gerar PDF e EPUB",
    ]

    if popular_titles:
        lines.extend(
            [
                "",
                "🔥 <b>Em alta agora</b>",
                *[f"• {html.escape(title)}" for title in popular_titles],
            ]
        )

    if updated_titles:
        lines.extend(
            [
                "",
                "🆕 <b>Atualizadas agora</b>",
                *[f"• {html.escape(title)}" for title in updated_titles],
            ]
        )

    lines.extend(
        [
            "",
            f"Digite <code>/buscar nome da HQ</code> ou <code>{html.escape(query_hint)}</code> para comecar.",
        ]
    )
    return "\n".join(lines)


def search_help_text() -> str:
    return (
        "🔎 <b>Buscar HQ</b>\n\n"
        "Envie o nome da HQ do jeito mais simples possivel.\n\n"
        "Exemplos:\n"
        "<code>/buscar batman</code>\n"
        "<code>/buscar homem aranha</code>"
    )


def empty_library_text(title: str, body: str) -> str:
    return f"📂 <b>{html.escape(title)}</b>\n\n{html.escape(body)}"


def help_text() -> str:
    return (
        "🛠 <b>Comandos principais</b>\n\n"
        "• <code>/start</code> abre a tela inicial\n"
        "• <code>/buscar nome</code> procura uma HQ\n"
        "• <code>/catalogo</code> abre as mais lidas\n"
        "• <code>/atualizacoes</code> mostra novidades\n"
        "• <code>/continuar</code> retoma sua leitura\n"
        "• <code>/favoritas</code> lista suas favoritas\n"
        "• <code>/historico</code> mostra seus acessos recentes\n\n"
        "Quando abrir um capitulo, o bot salva seu progresso automaticamente e pode gerar Telegraph, PDF e EPUB."
    )
