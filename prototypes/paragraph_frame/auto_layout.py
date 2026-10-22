"""Natural-width UAX14 wrapping, without font shrinking or frame expansion."""
from uniseg.linebreak import line_break_boundaries
from .engine import Refused


def layout(tokens,spec):
    text=''.join(t['text'] for t in tokens); breaks=set(line_break_boundaries(text));breaks.add(len(tokens))
    if not tokens:raise Refused('empty paragraph requires a paragraph-deletion policy')
    spans=[];start=0
    while start<len(tokens):
        ends=fit(tokens,text,breaks,start,spec['right']-(spec['first_x'] if not spans else spec['left']))
        if not ends:raise Refused('capacity: no legal line break at original font size')
        end=max(ends);spans.append((start,end));start=end
    spans=balance_tail(tokens,text,breaks,spans,spec['right']-spec['left'])
    result=[]
    for line,(a,b) in enumerate(spans):
        x=spec['first_x'] if not line else spec['left']
        offsets=spec.get('line_offsets',[0.])
        offset=offsets[line] if line<len(offsets) else offsets[-1]+(line-len(offsets)+1)*spec['line_height']
        y=spec['baseline']+offset;placed=[]
        extra=_justification(tokens[a:b],spec['right']-x) if spec.get('align')=='justify' and line<len(spans)-1 else _squeezed(tokens[a:b],spec['right']-x)
        for n,token in enumerate(tokens[a:b]):
            placed.append(dict(token,x=x,y=y));x+=token['advance']+extra.get(n,0)
        result.append(dict(text=text[a:b],baseline=y,tokens=placed,justified=bool(extra)))
    return result


def fit(tokens,text,breaks,start,width):
    """Legal line ends for a line starting at ``start`` that fit ``width`` at the original size."""
    used=0;ends=[];ink=0;squeeze=0
    for i in range(start,len(tokens)):
        used+=tokens[i]['advance']
        # Line-final Tc/Tw/TJ displacement does not occupy glyph ink, and
        # whitespace before a break hangs: neither can overflow the line.
        if not text[i].isspace():ink=used-tokens[i]['advance']+tokens[i].get('nominal_advance',tokens[i]['advance'])
        if i>start:squeeze+=tokens[i-1].get('squeezable',0)
        if ink>width+.05+squeeze:break
        if i+1 in breaks:ends.append(i+1)
    return ends


def balance_tail(tokens,text,breaks,spans,width):
    """Avoid a tiny final Japanese line by moving a legal suffix from the
    preceding line. No width, font size or paragraph boundaries are changed."""
    if len(spans)>1 and len(text[spans[-1][0]:].strip())<4:
        a,b=spans[-2];_,end=spans[-1]
        candidates=[i for i in breaks if a<i<b and len(text[i:end].strip())>=4 and sum(t['advance'] for t in tokens[i:end])<=width+.05]
        if candidates:split=max(candidates);spans=spans[:-2]+[(a,split),(split,end)]
    return spans


def _justification(tokens,width):
    """Equal extra advance for inner word spaces of a non-final justified line.

    Only ASCII word spaces stretch (CJK spacing keeps the observed tracking).
    A line needing more than one em per space keeps its natural spacing.
    """
    last=max((n for n,t in enumerate(tokens) if not t['text'].isspace()),default=-1)
    if last<0:return {}
    spaces=[n for n,t in enumerate(tokens[:last]) if t['text']==' ']
    ink=sum(t['advance'] for t in tokens[:last])+tokens[last].get('nominal_advance',tokens[last]['advance'])
    slack=width-ink
    if not spaces or slack<=0 or slack/len(spaces)>tokens[last]['size']:return {}
    return {n:slack/len(spaces) for n in spaces}


def _squeezed(tokens,width):
    """Negative advance for squeezable new glyphs of a line that overflows only by their allowance."""
    last=max((n for n,t in enumerate(tokens) if not t['text'].isspace()),default=-1)
    if last<1:return {}
    ink=sum(t['advance'] for t in tokens[:last])+tokens[last].get('nominal_advance',tokens[last]['advance'])
    room={n:t['squeezable'] for n,t in enumerate(tokens[:last]) if t.get('squeezable')}
    if ink<=width or not room:return {}
    ratio=min(1.,(ink-width)/sum(room.values()))
    return {n:-v*ratio for n,v in room.items()}
