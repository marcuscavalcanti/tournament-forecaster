from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "outputs"
WIDTH = 1600
HEIGHT = 1000
SCALE = 2

FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
DIAGRAM_CONTRACT_KEYS = ("recent_event_harness", "self_identification")


COLORS = {
    "bg": "#F7F9FC",
    "ink": "#172033",
    "muted": "#5F6B7A",
    "line": "#B8C4D6",
    "navy": "#172033",
    "blue": "#2F6FED",
    "teal": "#0F9F8F",
    "green": "#2EAD69",
    "amber": "#E5A021",
    "coral": "#E86D5A",
    "purple": "#7D5FFF",
    "white": "#FFFFFF",
    "soft_blue": "#EAF1FF",
    "soft_teal": "#E8FAF7",
    "soft_green": "#EAF8F0",
    "soft_amber": "#FFF5DC",
    "soft_coral": "#FFF0ED",
    "soft_purple": "#F2EFFF",
}


def _hex_to_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha)


class Canvas:
    def __init__(self) -> None:
        self.image = Image.new("RGBA", (WIDTH * SCALE, HEIGHT * SCALE), _hex_to_rgba(COLORS["bg"]))
        self.draw = ImageDraw.Draw(self.image)

    def font(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
        path = FONT_BOLD if bold else FONT_REGULAR
        return ImageFont.truetype(path, size * SCALE)

    def xy(self, box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        return tuple(int(v * SCALE) for v in box)

    def point(self, x: float, y: float) -> tuple[int, int]:
        return int(x * SCALE), int(y * SCALE)

    def rounded_rect(
        self,
        box: tuple[float, float, float, float],
        *,
        radius: int,
        fill: str,
        outline: str | None = None,
        width: int = 1,
        alpha: int = 255,
    ) -> None:
        self.draw.rounded_rectangle(
            self.xy(box),
            radius=radius * SCALE,
            fill=_hex_to_rgba(fill, alpha),
            outline=_hex_to_rgba(outline) if outline else None,
            width=width * SCALE,
        )

    def text(
        self,
        xy: tuple[float, float],
        text: str,
        *,
        font: ImageFont.FreeTypeFont,
        fill: str = COLORS["ink"],
        anchor: str | None = None,
    ) -> None:
        self.draw.text(self.point(*xy), text, font=font, fill=_hex_to_rgba(fill), anchor=anchor)

    def text_box(
        self,
        box: tuple[float, float, float, float],
        text: str,
        *,
        title: str | None = None,
        title_color: str = COLORS["ink"],
        body_color: str = COLORS["muted"],
        title_size: int = 26,
        body_size: int = 20,
        align: str = "center",
    ) -> None:
        x1, y1, x2, y2 = box
        pad_x = 24
        y = y1 + 22
        if title:
            title_font = self.font(title_size, bold=True)
            for line in _wrap_text(self.draw, title, title_font, (x2 - x1 - pad_x * 2) * SCALE):
                anchor = "ma" if align == "center" else None
                x = (x1 + x2) / 2 if align == "center" else x1 + pad_x
                self.text((x, y), line, font=title_font, fill=title_color, anchor=anchor)
                y += title_size + 6
            y += 6
        body_font = self.font(body_size)
        for line in _wrap_text(self.draw, text, body_font, (x2 - x1 - pad_x * 2) * SCALE):
            anchor = "ma" if align == "center" else None
            x = (x1 + x2) / 2 if align == "center" else x1 + pad_x
            self.text((x, y), line, font=body_font, fill=body_color, anchor=anchor)
            y += body_size + 7

    def arrow(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        color: str = COLORS["line"],
        width: int = 4,
    ) -> None:
        x1, y1 = start
        x2, y2 = end
        self.draw.line([self.point(x1, y1), self.point(x2, y2)], fill=_hex_to_rgba(color), width=width * SCALE)
        angle = math.atan2(y2 - y1, x2 - x1)
        size = 13
        left = (
            x2 - size * math.cos(angle) + size * 0.55 * math.sin(angle),
            y2 - size * math.sin(angle) - size * 0.55 * math.cos(angle),
        )
        right = (
            x2 - size * math.cos(angle) - size * 0.55 * math.sin(angle),
            y2 - size * math.sin(angle) + size * 0.55 * math.cos(angle),
        )
        self.draw.polygon([self.point(x2, y2), self.point(*left), self.point(*right)], fill=_hex_to_rgba(color))

    def save(self, path: Path) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        final = self.image.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS).convert("RGB")
        final.save(path, "PNG", optimize=True)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def draw_header(c: Canvas, title: str, subtitle: str, eyebrow: str) -> None:
    c.text((88, 66), eyebrow.upper(), font=c.font(18, bold=True), fill=COLORS["teal"])
    c.text((88, 106), title, font=c.font(44, bold=True), fill=COLORS["ink"])
    c.text((88, 160), subtitle, font=c.font(22), fill=COLORS["muted"])
    c.rounded_rect((1235, 82, 1490, 145), radius=18, fill=COLORS["navy"])
    c.text((1362, 108), "#CopaComAchismo", font=c.font(22, bold=True), fill=COLORS["white"], anchor="ma")


def card(
    c: Canvas,
    box: tuple[float, float, float, float],
    *,
    fill: str,
    stroke: str,
    title: str,
    body: str,
    title_color: str | None = None,
    body_color: str = COLORS["muted"],
    title_size: int = 25,
    body_size: int = 19,
) -> None:
    x1, y1, x2, y2 = box
    c.rounded_rect((x1 + 6, y1 + 7, x2 + 6, y2 + 7), radius=20, fill="#DCE4EF")
    c.rounded_rect(box, radius=20, fill=fill, outline=stroke, width=2)
    c.text_box(
        box,
        body,
        title=title,
        title_color=title_color or stroke,
        body_color=body_color,
        title_size=title_size,
        body_size=body_size,
    )


def build_technical() -> Path:
    c = Canvas()
    draw_header(
        c,
        "Engine técnica do debriefing",
        "Runner, modelos, duas salas, Monte Carlo 2 níveis, IC 99%, quórum e trilha auditável do run.",
        "Diagrama técnico",
    )

    card(
        c,
        (70, 245, 315, 385),
        fill=COLORS["white"],
        stroke=COLORS["blue"],
        title="ENTRADAS",
        body="Config, grupos, bracket, eventos, env/CLI/API, Makefile.",
        title_size=23,
        body_size=17,
    )
    card(
        c,
        (360, 245, 640, 385),
        fill=COLORS["navy"],
        stroke=COLORS["navy"],
        title="MEDIADOR",
        body="Carrega config, roda preflight 180s, distribui contrato único. Não faz fetch central.",
        title_color=COLORS["white"],
        body_color="#D6DFEA",
        title_size=23,
        body_size=17,
    )
    card(
        c,
        (685, 245, 980, 385),
        fill=COLORS["white"],
        stroke=COLORS["green"],
        title="PLANO DE FONTES",
        body="Modelos escolhem fontes próprias. Entrada: 3 planos; self-heal se faltar.",
        title_size=23,
        body_size=17,
    )
    card(
        c,
        (1025, 245, 1305, 385),
        fill=COLORS["soft_coral"],
        stroke=COLORS["coral"],
        title="RESILIÊNCIA",
        body="Preflight exclui slot morto. Timeout hard com margem. Reentrada seletiva e reparo curto de formato.",
        title_size=23,
        body_size=16,
    )
    card(
        c,
        (1345, 245, 1530, 415),
        fill=COLORS["soft_purple"],
        stroke=COLORS["purple"],
        title="GUARDS",
        body="Retry/backoff, bulkhead, anti-eco, breaker 3x, sala estéril aborta, IC 99 e gate pré-render.",
        title_size=23,
        body_size=15,
    )

    for start, end, color in [
        ((315, 315), (360, 315), COLORS["blue"]),
        ((640, 315), (685, 315), COLORS["green"]),
        ((980, 315), (1025, 315), COLORS["coral"]),
        ((1305, 315), (1345, 315), COLORS["purple"]),
    ]:
        c.arrow(start, end, color=color, width=4)

    c.rounded_rect((70, 470, 735, 725), radius=24, fill=COLORS["soft_blue"], outline=COLORS["blue"], width=2)
    c.text((105, 510), "SALA PRINCIPAL - BRASIL", font=c.font(27, bold=True), fill=COLORS["blue"])
    c.text((105, 552), "• Simula grupo, 16 avos, Oitavas, Quartas, Semi, Final e título", font=c.font(19), fill=COLORS["ink"])
    c.text((105, 586), "• Respostas terminam com consensus_check_question (pergunta de consenso)", font=c.font(19), fill=COLORS["ink"])
    c.text((105, 620), "• Quórum = floor(participantes ativos/2)+1", font=c.font(19), fill=COLORS["ink"])
    c.text((105, 654), "• Fallback sintético não vota; consenso exige voto válido", font=c.font(17), fill=COLORS["muted"])
    c.text((105, 678), "• Breaker 3x inválidas; líder sem voto perde a palavra;", font=c.font(17), fill=COLORS["muted"])
    c.text((122, 700), "estável 2 rodadas encerra cedo; sala vazia 2 rodadas aborta", font=c.font(17), fill=COLORS["muted"])

    c.rounded_rect((865, 470, 1530, 725), radius=24, fill=COLORS["soft_teal"], outline=COLORS["teal"], width=2)
    c.text((900, 510), "SALA PARALELA - ADVERSÁRIOS", font=c.font(27, bold=True), fill=COLORS["teal"])
    c.text((900, 552), "• Mesmos modelos e contrato; roda antes da sala Brasil", font=c.font(19), fill=COLORS["ink"])
    c.text((900, 586), "• Só candidatos oficiais do bracket por fase", font=c.font(19), fill=COLORS["ink"])
    c.text((900, 620), "• Produz scenario_probabilities e match_probabilities", font=c.font(19), fill=COLORS["ink"])
    c.text((900, 654), "• Anti-eco: cenário nunca vira vitória condicional", font=c.font(19), fill=COLORS["ink"])
    c.text((900, 686), "• Timeout de 900s não trava a principal: segue com Monte Carlo", font=c.font(17), fill=COLORS["muted"])

    c.arrow((980, 385), (455, 470), color=COLORS["blue"], width=5)
    c.arrow((980, 385), (1195, 470), color=COLORS["teal"], width=5)
    c.arrow((735, 585), (865, 585), color=COLORS["line"], width=4)

    c.rounded_rect((70, 780, 1530, 900), radius=24, fill=COLORS["white"], outline="#D7DEE9", width=2)
    c.text((105, 818), "SAÍDA AUDITÁVEL", font=c.font(26, bold=True), fill=COLORS["ink"])
    c.text((105, 860), "LinkedIn MD · JSON · watchdog.jsonl · custos USD/BRL · fontes · IC 99 · calibração · hard gate · nome/versão declarados", font=c.font(20), fill=COLORS["muted"])
    c.arrow((455, 725), (455, 780), color=COLORS["blue"], width=5)
    c.arrow((1195, 725), (1195, 780), color=COLORS["teal"], width=5)

    path = OUTPUT_DIR / "framework_technical_announcement.png"
    c.save(path)
    return path


def build_functional() -> Path:
    c = Canvas()
    draw_header(
        c,
        "Sala de debriefing funcional",
        "O post nasce da reunião: contrato, modelos, sala paralela, IC 99, calibração e saída auditável.",
        "Diagrama funcional",
    )

    card(
        c,
        (70, 250, 330, 400),
        fill=COLORS["soft_blue"],
        stroke=COLORS["blue"],
        title="OBJETIVO",
        body="Até onde o Brasil deve chegar? Grupo, 16 avos, Oitavas, Quartas, Semi, Final e título.",
        title_size=22,
        body_size=16,
    )
    card(
        c,
        (370, 225, 655, 400),
        fill=COLORS["soft_green"],
        stroke=COLORS["green"],
        title="CONTRATO ÚNICO",
        body="Quanti e quali sem quota fixa. Mesmas regras, fontes próprias e sem fetch central do mediador.",
        title_size=22,
        body_size=16,
    )
    card(
        c,
        (695, 225, 980, 400),
        fill=COLORS["soft_amber"],
        stroke=COLORS["amber"],
        title="MODELOS NA MESA",
        body="GPT, Claude, Gemini, Perplexity e DeepSeek chegam com pesquisa fresca e auditável.",
        title_size=22,
        body_size=16,
    )
    card(
        c,
        (1020, 225, 1305, 400),
        fill=COLORS["soft_purple"],
        stroke=COLORS["purple"],
        title="EVENTOS RECENTES",
        body="Amistosos, cortes, cartões, descanso, arbitragem, imprensa e impacto por fonte/data.",
        title_size=22,
        body_size=16,
    )
    card(
        c,
        (1345, 250, 1530, 400),
        fill=COLORS["white"],
        stroke=COLORS["navy"],
        title="WATCHDOG",
        body="Mostra contrato, falhas, custo, rodadas e reentrada.",
        title_size=22,
        body_size=16,
    )

    c.rounded_rect((365, 470, 1235, 750), radius=34, fill=COLORS["white"], outline="#D7DEE9", width=3)
    c.rounded_rect((445, 515, 1155, 700), radius=42, fill=COLORS["navy"], outline=COLORS["navy"], width=2)
    c.text((800, 544), "MESA DE DEBRIEFING", font=c.font(29, bold=True), fill=COLORS["white"], anchor="ma")
    c.text((800, 582), "Líder pergunta; pares respondem; liderança roda por mérito", font=c.font(18), fill="#D6DFEA", anchor="ma")
    c.text((800, 614), "Fala sem fonte não vale; adversário impossível é anulado", font=c.font(17), fill="#D6DFEA", anchor="ma")
    c.text((800, 642), "Líder sem voto perde a palavra; estável 2 rodadas encerra cedo", font=c.font(17), fill="#D6DFEA", anchor="ma")
    c.text((800, 670), "Sala vazia 2 rodadas: sessão cai barato, sem queimar horas", font=c.font(17), fill="#D6DFEA", anchor="ma")

    participants = [
        ((410, 440, 565, 500), COLORS["soft_blue"], COLORS["blue"], "GPT", "OpenAI CLI"),
        ((610, 440, 765, 500), COLORS["soft_purple"], COLORS["purple"], "CLAUDE", "CLI high"),
        ((810, 440, 965, 500), COLORS["soft_green"], COLORS["green"], "GEMINI", "CLI/API"),
        ((1010, 440, 1190, 500), COLORS["soft_amber"], COLORS["amber"], "PERPLEXITY", "API"),
        ((705, 715, 895, 775), COLORS["soft_teal"], COLORS["teal"], "DEEPSEEK", "API v4 pro"),
    ]
    for box, fill, stroke, title, body in participants:
        c.rounded_rect(box, radius=18, fill=fill, outline=stroke, width=2)
        x1, y1, x2, _ = box
        c.text(((x1 + x2) / 2, y1 + 15), title, font=c.font(16, bold=True), fill=stroke, anchor="ma")
        c.text(((x1 + x2) / 2, y1 + 38), body, font=c.font(14), fill=COLORS["muted"], anchor="ma")

    card(
        c,
        (70, 545, 325, 730),
        fill=COLORS["soft_teal"],
        stroke=COLORS["teal"],
        title="SALA PARALELA",
        body="Adversários prováveis por fase. Bracket oficial + Monte Carlo 2 níveis; prior baixo aciona gate de confiança.",
        title_size=22,
        body_size=16,
    )
    card(
        c,
        (1275, 545, 1530, 730),
        fill=COLORS["soft_blue"],
        stroke=COLORS["blue"],
        title="SALA BRASIL",
        body="Probabilidade jogo a jogo contra os dois cenários de cada fase até a Final.",
        title_size=22,
        body_size=16,
    )
    card(
        c,
        (380, 805, 735, 965),
        fill=COLORS["soft_coral"],
        stroke=COLORS["coral"],
        title="RODADA DE CONSENSO",
        body="Maioria simples dos ativos. Só voto válido conta: fallback sintético nunca vira consenso.",
        title_size=21,
        body_size=16,
    )
    c.rounded_rect((955, 805, 1510, 965), radius=22, fill=COLORS["navy"], outline=COLORS["navy"], width=2)
    c.text((1232, 835), "POST LINKEDIN", font=c.font(24, bold=True), fill=COLORS["white"], anchor="ma")
    c.text((995, 874), "• caminho até o hexa: funil único da simulação + sala", font=c.font(16), fill="#D6DFEA")
    c.text((995, 902), "• chat resumido, debate auditável e IC honesto (99%)", font=c.font(16), fill="#D6DFEA")
    c.text((995, 930), "• só publica após gate de coerência; recalcula a cada véspera", font=c.font(16), fill="#D6DFEA")

    c.arrow((330, 325), (520, 470), color=COLORS["blue"], width=4)
    c.arrow((655, 315), (690, 470), color=COLORS["green"], width=4)
    c.arrow((840, 400), (840, 470), color=COLORS["amber"], width=4)
    c.arrow((1020, 315), (970, 470), color=COLORS["purple"], width=4)
    c.arrow((1345, 330), (1180, 505), color=COLORS["navy"], width=4)
    c.arrow((325, 640), (365, 640), color=COLORS["teal"], width=5)
    c.arrow((1235, 640), (1275, 640), color=COLORS["blue"], width=5)
    c.arrow((800, 700), (675, 805), color=COLORS["coral"], width=5)
    c.arrow((735, 885), (955, 885), color=COLORS["green"], width=5)

    path = OUTPUT_DIR / "framework_functional_announcement.png"
    c.save(path)
    return path


def main() -> None:
    paths = [build_technical(), build_functional()]
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
