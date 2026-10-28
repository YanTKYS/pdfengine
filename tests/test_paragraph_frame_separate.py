"""B2: a line below a growing paragraph drawn in one text object with other text is separated from it on explicit approval (issue #105)."""
import re
from test_paragraph_frame_tagged import LINES, tagged

ROW = re.compile(rb'q\nBT\n1 0 0 1 ([\d.]+) ([\d.]+) Tm\n/Fixture 11 Tf \[<([0-9a-f]+)>\]TJ\nET\nQ')
# The paragraph, an indented line below it (no following paragraph of it) and
# a heading at the top of the page; the line and the heading are drawn in one
# text object (SHARED).
SHARED = LINES[:3] + [(52, 112, '問合せ先は窓口です。'), (40, 24, '本年度の補助事業')]
GROW = ('申請書は窓口で', '申請書は窓口と郵送と電子申請とファクスと持参で')


def shared(tmp_path, name='shared.pdf', order='heading-first', inside=b'', after=b'', icon=False, clip=None, mixed=False):
    """SHARED with the line (stream 3) and the heading (stream 4) in one text object.

    ``order``: 'heading-first' (Tm, heading, Tm, line) or 'line-first' (Tm,
    line, Td to the heading, heading). ``inside`` is written in the text
    object between the two, ``after`` right after the line's operator (HEAD:
    the heading's first glyph code);
    ``icon`` paints a box right before the text object, on the line; ``clip``
    (a clip path) encloses the text object; ``mixed`` draws the heading and the
    line with one TJ (a kerning jump between them)."""
    def change(data):
        line, head = ROW.search(data[3]), ROW.search(data[4])
        lx, ly, lc = float(line.group(1)), float(line.group(2)), line.group(3)
        hx, hy, hc = float(head.group(1)), float(head.group(2)), head.group(3)
        show = lambda codes: b'[<' + codes + b'>]TJ\n'
        after_ = after.replace(b'HEAD', hc[:4])
        if mixed:
            body = f'1 0 0 1 {lx:g} {ly:g} Tm\n'.encode() + b'[<' + lc + b'> -6000 <' + hc + b'>]TJ\n'
        elif order == 'heading-first':
            body = f'1 0 0 1 {hx:g} {hy:g} Tm\n'.encode() + show(hc) + inside + f'1 0 0 1 {lx:g} {ly:g} Tm\n'.encode() + show(lc) + after_
        else:
            body = (f'1 0 0 1 {lx:g} {ly:g} Tm\n'.encode() + show(lc) + after_ + inside
                    + f'{hx - lx:g} {hy - ly:g} Td\n'.encode() + show(hc))
        text = b'BT\n/Fixture 11 Tf\n' + body + b'ET\n'
        if clip:
            text = b'q ' + clip + b' W n\n' + text + b'Q\n'
        box = f'.9 g {lx - 2:g} {ly - 3:g} 8 12 re f\n'.encode() if icon else b''
        data[3] = data[3][:line.start()] + b'q\n' + box + text + b'Q' + data[3][line.end():]
        data[4] = data[4][:head.start()] + data[4][head.end():]
        return data
    source, _ = tagged(tmp_path, [], lines=SHARED, tree=False, edit_streams=change, name=name)
    return source


def fixture(name, folder):
    """The generated PDFs of the separation evaluation (separate_evaluate)."""
    folder.mkdir(parents=True, exist_ok=True)
    return dict(
        # The heading first, then the line: the line is the text object's last operator.
        G1=lambda: shared(folder),
        # The line first, then the heading placed by Td; a box on the line drawn before the text object.
        G2=lambda: shared(folder, order='line-first', icon=True),
        # One TJ draws the line and the heading: no operator is cut.
        G3=lambda: shared(folder, mixed=True),
        # Marked content in the text object between the heading and the line.
        G4=lambda: shared(folder, inside=b'/Tag BMC EMC\n'),
        # A glyph of the heading continues from where the line ends (raised by Ts, no positioning).
        G5=lambda: shared(folder, order='line-first', after=b'200 Ts\n[<HEAD>]TJ\n0 Ts\n'),
        # A clip around the text object that ends right below the line.
        G6=lambda: shared(folder, clip=b'0 182 300 118 re'),
    )[name]()
