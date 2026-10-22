"""Natural-width UAX14 wrapping, without font shrinking or frame expansion."""
from uniseg.linebreak import line_break_boundaries
from .engine import Refused


def layout(tokens,spec):
    text=''.join(t['text'] for t in tokens); breaks=set(line_break_boundaries(text));breaks.add(len(tokens))
    if not tokens:raise Refused('empty paragraph requires a paragraph-deletion policy')
    spans=[];start=0
    while start<len(tokens):
        width=spec['right']-(spec['first_x'] if not spans else spec['left']);used=0;ends=[]
        for i in range(start,len(tokens)):
            used+=tokens[i]['advance']
            # Line-final Tc/Tw/TJ displacement does not occupy glyph ink.
            # Whitespace at paragraph end has no ink; keep its character and
            # source width but do not force a spurious whitespace-only line.
            effective=used-tokens[i]['advance'] if not text[i:].strip() else used-tokens[i]['advance']+tokens[i].get('nominal_advance',tokens[i]['advance'])
            if effective>width+.05:break
            if i+1 in breaks:ends.append(i+1)
        if not ends:raise Refused('capacity: no legal line break at original font size')
        end=max(ends);spans.append((start,end));start=end
    # Avoid a tiny final Japanese line by moving a legal suffix from the
    # preceding line. No width, font size or paragraph boundaries are changed.
    if len(spans)>1 and len(text[spans[-1][0]:].strip())<4:
        a,b=spans[-2];_,end=spans[-1]
        candidates=[i for i in breaks if a<i<b and len(text[i:end].strip())>=4 and sum(t['advance'] for t in tokens[i:end])<=spec['right']-spec['left']+.05]
        if candidates:split=max(candidates);spans[-2:]=[(a,split),(split,end)]
    result=[]
    for line,(a,b) in enumerate(spans):
        x=spec['first_x'] if not line else spec['left']
        offsets=spec.get('line_offsets',[0.])
        offset=offsets[line] if line<len(offsets) else offsets[-1]+(line-len(offsets)+1)*spec['line_height']
        y=spec['baseline']+offset;placed=[]
        for token in tokens[a:b]:
            placed.append(dict(token,x=x,y=y));x+=token['advance']
        result.append(dict(text=text[a:b],baseline=y,tokens=placed))
    return result
