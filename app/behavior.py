from __future__ import annotations
import logging, re
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
logger = logging.getLogger('vibelenz.behavior')
PRESSURE_THRESHOLD = 0.35
ASYMMETRY_THRESHOLD = 0.25
_FIN = ['send','wire','transfer','pay','venmo','cashapp','zelle','paypal','bitcoin','crypto','deposit','money','cash','funds','payment','invest','wallet','gift card']
_URG = ['urgent','urgently','immediately','right now','asap','act now','limited time','today only','deadline','hurry','last chance','must act']
_ISO = ['dont tell','do not tell','keep this between us','our secret','just between us','no one needs to know','dont tell anyone','tell no one','keep this private']
_QRE = re.compile(r'\?')
_WRE = re.compile(r'\b\w+\b')
_FUT = re.compile(r'\b(next time|tomorrow|weekend|soon|looking forward|plan)\b', re.IGNORECASE)
_CAL = re.compile(r'\b(remember|you said|last time|earlier)\b', re.IGNORECASE)
def _clamp(v,lo=0.0,hi=1.0): return max(lo,min(hi,v))
def _norm(t): return ' '.join((t or '').lower().split())
def _hits(text,phrases): t=_norm(text); return sum(1 for p in phrases if p in t)
@dataclass
class BehaviorProfile:
    reciprocity_score:float=0.0
    initiative_score:float=0.0
    engagement_depth_score:float=0.0
    continuity_score:float=0.0
    forward_movement_score:float=0.0
    pressure_score:float=0.0
    isolation_score:float=0.0
    urgency_score:float=0.0
    asymmetry_score:float=0.0
    financial_mentions:int=0
    urgency_mentions:int=0
    isolation_mentions:int=0
    pressure_phrase_hits:int=0
    deterministic_flag:bool=False
    turn_count:int=0
    other_turn_count:int=0
    user_turn_count:int=0
    degraded:bool=False
    def _active_flags(self):
        f=[]
        if self.pressure_score>0.0: f.append('pressure_present')
        if self.urgency_score>0.3: f.append('urgency_detected')
        if self.isolation_score>0.3: f.append('isolation_detected')
        if self.financial_mentions>0: f.append('financial_mention')
        if self.deterministic_flag: f.append('deterministic_gate_triggered')
        return f
    def to_schema_dict(self):
        f=self._active_flags()
        return {'risk_score':round(self.pressure_score,4),'flags':f,'confidence':0.5 if self.degraded else round(min(1.0,0.55+len(f)*0.07),2),'degraded':self.degraded,'pressure_score':round(self.pressure_score,4),'isolation_score':round(self.isolation_score,4),'urgency_score':round(self.urgency_score,4),'asymmetry_score':round(self.asymmetry_score,4),'deterministic_flag':self.deterministic_flag}
    def to_feature_vector(self):
        return {'reciprocity_score':self.reciprocity_score,'initiative_score':self.initiative_score,'engagement_depth_score':self.engagement_depth_score,'continuity_score':self.continuity_score,'forward_movement_score':self.forward_movement_score,'pressure_score':self.pressure_score,'isolation_score':self.isolation_score,'urgency_score':self.urgency_score,'asymmetry_score':self.asymmetry_score,'financial_mentions':self.financial_mentions,'urgency_mentions':self.urgency_mentions,'isolation_mentions':self.isolation_mentions,'deterministic_flag':self.deterministic_flag,'turn_count':self.turn_count}
    def to_dict(self): return asdict(self)
class BehaviorExtractor:
    def extract(self,turns):
        if not turns: return BehaviorProfile()
        try: return self._extract(turns)
        except Exception as e: logger.error('failed: %s',e); return BehaviorProfile(degraded=True)
    def _extract(self,turns):
        ut=[t for t in turns if t.get('sender')=='user']
        ot=[t for t in turns if t.get('sender')=='other']
        if not ut and not ot: ut=turns[::2]; ot=turns[1::2]
        tot=len(turns); uc=len(ut); oc=len(ot)
        aot=' '.join(t.get('text','') for t in ot)
        at=' '.join(t.get('text','') for t in turns)
        fin=_hits(aot,_FIN); urg=_hits(aot,_URG); iso=_hits(aot,_ISO)
        us=_clamp(urg/4.0); is_=_clamp(iso/3.0)
        ps=_clamp(_clamp(fin/3.0)*0.50+us*0.30+is_*0.20)
        rec=_clamp(min(uc,oc)/max(max(uc,oc),1)) if tot>0 else 0.0
        flag=ps>=PRESSURE_THRESHOLD and rec<=ASYMMETRY_THRESHOLD
        qc=sum(len(_QRE.findall(t.get('text',''))) for t in ot)
        ini=_clamp(qc/max(tot,1))
        avg=sum(len(_WRE.findall(t.get('text',''))) for t in turns)/max(len(turns),1)
        eng=_clamp(avg/50.0)
        con=_clamp(len(_CAL.findall(at))/3.0)
        fwd=_clamp(len(_FUT.findall(at))/5.0)
        asy=_clamp(oc/tot) if tot>0 else 0.0
        return BehaviorProfile(reciprocity_score=round(rec,4),initiative_score=round(ini,4),engagement_depth_score=round(eng,4),continuity_score=round(con,4),forward_movement_score=round(fwd,4),pressure_score=round(ps,4),isolation_score=round(is_,4),urgency_score=round(us,4),asymmetry_score=round(asy,4),financial_mentions=fin,urgency_mentions=urg,isolation_mentions=iso,pressure_phrase_hits=0,deterministic_flag=flag,turn_count=tot,other_turn_count=oc,user_turn_count=uc,degraded=False)
_extractor=BehaviorExtractor()
def analyze_behavior(turns):
    return _extractor.extract(turns).to_schema_dict()
