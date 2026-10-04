"""Pure P4 transition/publication model. No runtime API/schema or authentication.

Trusted caller selects the target and persisted semantic record. Only the
current request authorizes changes. Checksums detect damage, not caller malice.
PDF construction/physical verification and ownership are explicit adapters.
"""
from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction as F
from io import BytesIO
from fontTools.ttLib import TTFont

from pdfeditor.shaped_font import ShapedFont
from evaluations.anchors.measurement_authority import edit_record, layout
from evaluations.anchors.semantic_binding import canonical, sha, font_policy, nominal_glyph, require


STAGES=('VerifyOld','AuthorizeTransition','BuildSemanticState','BuildCanonicalLayout',
        'BuildCandidatePDF','VerifyCandidatePhysicalBinding','BuildCandidateRecord','SealIntegrity',
        'PublishPair','Committed')
_VERIFIED=object()
_AUTHORIZED=object()


def derive(semantic, asset):
    """Recompute all derived current data from exact semantic authority."""
    require(set(semantic)=={'text','style','font','body_style_id','edges','region'},'SEMANTIC_FIELDS')
    require(semantic['font']==dict(sha=sha(asset),policy=font_policy()),'FONT_ASSOCIATION')
    raw=TTFont(BytesIO(asset),recalcTimestamp=False)
    try:require('fvar' not in raw,'VARIABLE_FONT_REFUSED')
    finally:raw.close()
    require(isinstance(semantic['body_style_id'],str) and 0<len(semantic['body_style_id'])<=64,'BODY_STYLE')
    font=ShapedFont(asset)
    try:
        text=semantic['text'];style=semantic['style'];region=semantic['region']
        require(isinstance(text,str) and len(text)<=256 and set(text)<=set('AB \n'),'TEXT_SCOPE')
        require(set(region)=={'x','baseline','width','leading'},'REGION_SCOPE')
        size,rise=F(style['font_size']),F(style['rise'])
        model=dict(alignment='left',glyphs=[{'text':'\n'} if c=='\n' else nominal_glyph(c,font) for c in text],
            style=deepcopy(style),edges=deepcopy(semantic['edges']),**region,
            empty=dict(ascent=str(max(F(0),F(font.font['hhea'].ascent,font.upem)*size-rise)),
                       descent=str(max(F(0),-F(font.font['hhea'].descent,font.upem)*size+rise))))
        plan=layout(model);emitted={g['start'] for g in plan['glyphs']}
        # body_style_id is caller metadata naming the single semantic style.
        # Renaming it does not change style values or the canonical "body" slot
        # shared by interval/omitted/default/empty associations in the PR39 adapter.
        record=dict(model=model,font_policy=font_policy(),
            codebook={c:i+1 for i,c in enumerate(sorted(set(text.replace('\n',''))|{'A',' '}))},
            intervals=[dict(start=i,end=i+1,id=f'current:{i}',style='body') for i in range(len(text))],
            omitted=[dict(offset=i,kind='newline' if c=='\n' else 'trimmed-space',style='body')
                     for i,c in enumerate(text) if i not in emitted],
            default_style_id='body',empty_style_id='body',paragraph_id='paragraph-1',pdf_sha='unbound')
        from evaluations.anchors.semantic_binding import admit
        checked,_=admit(record,asset);checked.font.close()
        return record,plan
    finally:font.font.close()


def seal(record):
    r=deepcopy(record);r.pop('checksum',None)
    r['checksum']=sha(canonical(r));return r


def integrity(record):
    r=deepcopy(record);claimed=r.pop('checksum',None)
    require(claimed==sha(canonical(r)),'RECORD_INTEGRITY')


@dataclass(frozen=True)
class Verified:
    record: bytes
    pdf: bytes
    asset: bytes
    token: object


@dataclass(frozen=True)
class Authorized:
    old: Verified
    request: bytes
    expected_semantic: bytes
    asset: bytes
    classification: str
    token: object


def unpack(data):
    import json
    return json.loads(data)


def verify_current(pdf, record, asset, target, physical):
    integrity(record)
    require(set(record)=={'version','target','semantic','derived','pdf_sha','owner','checksum'},'RECORD_SCOPE')
    require(record['version']==1 and record['target']==target,'CURRENT_TARGET')
    require(record['pdf_sha']==sha(pdf),'STALE_PDF')
    derived,plan=derive(record['semantic'],asset)
    require(record['derived']==derived,'DERIVED_RECORD_MISMATCH')
    physical(pdf,derived,asset,plan)
    return Verified(canonical(record),pdf,asset,_VERIFIED)


def initial_confirmation(pdf, semantic, asset, target, owner, physical, request):
    require(request=={'operation':'confirm','semantic':semantic},'INITIAL_CONFIRMATION_REQUIRED')
    derived,plan=derive(semantic,asset);physical(pdf,derived,asset,plan)
    record=seal(dict(version=1,target=deepcopy(target),semantic=deepcopy(semantic),
                     derived=derived,pdf_sha=sha(pdf),owner=deepcopy(owner)))
    return verify_current(pdf,record,asset,target,physical)


def authorize_transition(old, request, *, supplied_asset=None):
    require(isinstance(old,Verified) and old.token is _VERIFIED,'OLD_NOT_VERIFIED')
    require(isinstance(request,dict) and 'operation' in request,'REQUEST_REQUIRED')
    operation=request['operation'];current=unpack(old.record)['semantic'];new=deepcopy(current)
    asset=old.asset;classification='D'
    if operation=='save':
        require(set(request)=={'operation'},'REQUEST_FIELDS')
    elif operation=='edit':
        require(set(request)=={'operation','start','end','text'},'REQUEST_FIELDS')
        a,b=request['start'],request['end'];text=request['text']
        require(type(a) is int and type(b) is int and 0<=a<=b<=len(new['text']),'EDIT_RANGE')
        require(isinstance(text,str),'EDIT_TEXT')
        # Only adjacency policy derives edge removal/remapping; no gap inference.
        cells={'glyphs':[{'text':c} for c in new['text']],'edges':new['edges']}
        changed=edit_record(cells,a,b,[{'text':c} for c in text])
        new['text']=new['text'][:a]+text+new['text'][b:];new['edges']=changed['edges']
    elif operation=='reflow':
        require(set(request)=={'operation','width'},'REQUEST_FIELDS')
        require(isinstance(request['width'],str),'EXACT_WIDTH_REQUIRED')
        new['region']['width']=str(F(request['width']))
    elif operation=='reinterpret':
        require(set(request)=={'operation','changes'} and request['changes'],'EXPLICIT_CHANGES_REQUIRED')
        changes=request['changes'];classification='E'
        allowed={'font','font_size','horizontal_scale','rise','tracking','word_spacing','edges','body_style_id'}
        require(set(changes)<=allowed,'UNSUPPORTED_REINTERPRETATION')
        for key,value in changes.items():
            if key=='font':
                require(supplied_asset is not None and value==sha(supplied_asset),'EXPLICIT_ASSET_REQUIRED')
                asset=supplied_asset;new['font']=dict(sha=value,policy=font_policy())
            elif key in ('edges','body_style_id'):new[key]=deepcopy(value)
            else:
                require(isinstance(value,str),'EXACT_STYLE_REQUIRED')
                new['style'][key]=str(F(value))
    else:raise ValueError('REFUSED_TRANSITION:'+str(operation))
    require(supplied_asset is None or operation=='reinterpret' and 'font' in request.get('changes',{}),
            'UNREQUESTED_ASSET_CHANGE')
    derive(new,asset) # Scope + positive metric/edge/empty-style validation.
    return Authorized(old,canonical(request),canonical(new),asset,classification,_AUTHORIZED)


def semantic_diff(old,new):
    result={}
    for key in sorted(set(old)|set(new)):
        if old.get(key)!=new.get(key):
            if isinstance(old.get(key),dict) and isinstance(new.get(key),dict):
                result.update({key+'.'+k:v for k,v in semantic_diff(old[key],new[key]).items()})
            else:result[key]=dict(before=old.get(key),after=new.get(key))
    return result


def check_diff(authorization,candidate):
    require(isinstance(authorization,Authorized) and authorization.token is _AUTHORIZED,'NOT_AUTHORIZED')
    require(canonical(candidate)==authorization.expected_semantic,'UNAUTHORIZED_SEMANTIC_DIFF')
    return semantic_diff(unpack(authorization.old.record)['semantic'],candidate)


def check_owner(old, requested_owner, *, overlap=False):
    # Evidence-model adapter: the real owner witness must independently verify
    # exact current source-output spans/context before MutationProgram accepts it.
    owner=unpack(old.record)['owner']
    require(owner['domain']=='source-output' and owner['id']==requested_owner and not overlap,'PHYSICAL_OWNERSHIP')
    return owner


class PairStore:
    """Pure publish-new-paths model; formal visibility switches after both links.

    Simulates exclusive caller access. Not a filesystem/crash-atomic publisher.
    Old artifacts never change. Incomplete targets cannot form a current bundle.
    """
    def __init__(self,pdf,record):
        self.current=(pdf,deepcopy(record));self.targets={};self.events=[]

    def publish(self,pdf,record,*,order='pdf-first',fail=None):
        old=self.current
        try:
            for part in (('pdf','record') if order=='pdf-first' else ('record','pdf')):
                self.targets[part]=pdf if part=='pdf' else deepcopy(record)
                self.events.append(dict(after=part,current='old',complete=len(self.targets)==2))
                if fail=='after_'+part:raise ValueError('INJECTED:after_'+part)
            integrity(self.targets['record'])
            require(self.targets['record']['pdf_sha']==sha(self.targets['pdf']),'PUBLISHED_PAIR_BINDING')
            self.current=(pdf,deepcopy(record))
        except Exception:
            self.targets.clear();self.current=old;raise


def run(store,request,asset,target,*,physical,build,owner,fail=None,order='pdf-first',
        supplied_asset=None,overlap=False,tamper=None):
    """S0–S9 with separate adapters. No receipt/key or self-confirmation step."""
    trace=[]
    def point(name):
        trace.append(name)
        if fail==name:raise ValueError('INJECTED:'+name)
    old=verify_current(*store.current,asset,target,physical);point('VerifyOld')
    auth=authorize_transition(old,request,supplied_asset=supplied_asset)
    authority=check_owner(old,owner,overlap=overlap);point('AuthorizeTransition')
    semantic=unpack(auth.expected_semantic)
    if tamper:tamper(semantic)
    diff=check_diff(auth,semantic);point('BuildSemanticState')
    derived,plan=derive(semantic,auth.asset);point('BuildCanonicalLayout')
    pdf=build(derived,plan,auth.asset);point('BuildCandidatePDF')
    physical(pdf,derived,auth.asset,plan);point('VerifyCandidatePhysicalBinding')
    record=dict(version=1,target=deepcopy(target),semantic=semantic,derived=derived,
                pdf_sha=sha(pdf),owner=authority);point('BuildCandidateRecord')
    record=seal(record);point('SealIntegrity')
    # Full new bundle verification before either artifact becomes current.
    verify_current(pdf,record,auth.asset,target,physical)
    point('PublishPair')
    store.publish(pdf,record,order=order,fail=fail)
    trace.append('Committed')
    if fail=='Committed':raise ValueError('INJECTED:Committed')
    return dict(trace=trace,classification=auth.classification,diff=diff,
                semantic_exact=canonical(semantic)==auth.expected_semantic,asset=auth.asset)


def verdict(gates):
    required={
        'model_determinism':{'exact_derived'},
        'input_admissibility':{'scoped_physical'},
        'authenticated_binding':{'current_binding','semantic_transition_authority',
            'physical_mutation_ownership','candidate_reverification','atomic_pair_publication'}}
    for layer,names in required.items():
        results=gates.get(layer,{})
        if not names<=results.keys() or any(v is not True for v in results.values()):
            return 'NOT READY'
    return 'DESIGN READY FOR SEPARATE LAYOUT IMPLEMENTATION PR'
