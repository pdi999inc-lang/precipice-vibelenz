from __future__ import annotations
from typing import Any, Dict, List, Optional
def _clean(items):
    return [str(x).strip() for x in (items or []) if str(x).strip()]
def _human_label(primary_label, lane, domain_mode):
    mapping = {'playful_reengagement':'playful reconnection','light_sexual_reciprocity':'light sexual reciprocity','warm_receptivity':'warm receptivity','confusion_then_repair':'confusion that clears','casual_flirtation':'casual flirtation','low_information_neutral':'low-stakes interaction','routine_host_message':'routine logistics','routine_message':'routine message','relationship_context':'relationship context','mixed_intent':'mixed intent','transactional_extraction_pattern':'transactional risk pattern','pressure_with_boundary_violation':'pressure pattern','MIXED_INTENT':'mixed signals','NEGATIVE':'negative signals'}
    return mapping.get(primary_label, primary_label.replace('_',' '))
def _social_tone(result):
    positives = _clean(result.get('positive_signals',[]))
    primary_label = str(result.get('primary_label','low_information_neutral'))
    connection_level = str(result.get('connection_level','')).upper()
    if connection_level == 'NEGATIVE': return 'disengaged or resistant'
    if connection_level == 'MIXED_INTENT': return 'mixed — positive and negative signals both present'
    if 'sexual_reciprocity_present' in positives or primary_label == 'light_sexual_reciprocity': return 'playful, flirtatious, and reciprocal'
    if primary_label == 'playful_reengagement': return 'warm after initial confusion'
    if 'warm_receptivity_present' in positives or primary_label == 'warm_receptivity': return 'open, warm, and responsive'
    if primary_label == 'confusion_then_repair': return 'awkward at first, then repaired'
    if primary_label == 'casual_flirtation': return 'light and socially positive'
    return 'fairly low-stakes'
def _interest_summary(result):
    label = str(result.get('interest_label','Not Applicable'))
    primary_label = str(result.get('primary_label','low_information_neutral'))
    connection_level = str(result.get('connection_level','')).upper()
    if connection_level == 'NEGATIVE': return 'disengagement detected'
def _risk_copy(out):
    lane = str(out.get('lane','BENIGN'))
    domain_mode = str(out.get('domain_mode','general_unknown'))
    if lane == 'FRAUD' and domain_mode == 'housing_rental':
        diagnosis = 'This looks more like a setup than a normal rental conversation.'
        reasoning = 'The concern is the sequence. Once verification gets inverted, money enters the picture, or the story starts shifting, the interaction stops reading like normal logistics and starts reading like a transactional risk pattern.'
        next_steps = 'Slow it down immediately. Verify ownership, identity, and the platform story independently before you give money, documents, or trust.'
        accountability = 'Do not talk yourself out of obvious risk just because the tone sounds polite, charming, or routine.'
    elif lane == 'FRAUD':
        diagnosis = 'This reads more like a risk pattern than a normal interaction.'
        reasoning = 'What matters most is not one isolated line, but the overall pattern of pressure, extraction, contradiction, or control.'
        next_steps = 'Pause the interaction and verify independently before you give money, sensitive information, or control.'
        accountability = 'Do not explain away real risk signals just because the delivery feels smooth.'
    elif lane == 'COERCION_RISK':
        diagnosis = 'This is starting to feel like pressure, not just awkwardness.'
        reasoning = 'What pushes this upward is not mere discomfort. The visible pattern starts to lean on pressure or boundary friction, which matters more than tone alone.'
        next_steps = 'Tighten the boundary. State it clearly once, then watch whether the other person respects it without needing a long argument.'
        accountability = 'Do not explain away pressure just because it arrives wrapped in charm, confusion, or emotion.'
    else:
        diagnosis = 'This does not currently read like a strong risk pattern, but the signals are not clean.'
        reasoning = 'Nothing here strongly supports fraud or coercion as the main story, but enough signals are present to warrant attention.'
        next_steps = 'Stay observant. Do not overreact, but do not ignore the signals that are present.'
        accountability = 'Do not manufacture danger — and do not dismiss real signals either.'
    out['presentation_mode']='risk'; out['mode_title']='Risk Analysis'
    out['mode_tagline']='Sharper read on contradiction, pressure, extraction, and protective next steps.'
    out['human_label']=_human_label(str(out.get('primary_label','')),lane,domain_mode)
    out['diagnosis']=diagnosis; out['reasoning']=reasoning
    out['practical_next_steps']=next_steps; out['accountability']=accountability
    out['social_tone']='Not the focus here'; out['interest_summary']='Not the focus here'
    out['mode_override_note']=''
    return out
def _connection_copy(out, relationship_type='stranger'):
    primary_label = str(out.get('primary_label','low_information_neutral'))
    connection_level = str(out.get('connection_level','')).upper()
    if connection_level == 'NEGATIVE':
        diagnosis = 'The signals here are more resistant than receptive.'
        reasoning = 'What is visible is not just a quiet or low-energy response — there are active signals of discomfort, disengagement, or pushback.'
        next_steps = 'Give the conversation room to breathe. Pressing harder against clear resistance will not help.'
        accountability = 'Receptivity has to be present before chemistry can build. Do not talk yourself into a positive read when the signals are pointing the other way.'
    elif connection_level == 'MIXED_INTENT':
        diagnosis = 'There are positive and negative signals present at the same time — this one needs a careful read.'
        reasoning = 'The conversation contains both warmth and friction, interest and resistance. That does not mean it is bad or good — it means the picture is genuinely mixed.'
        next_steps = 'Watch the direction of movement, not just the snapshot. Is the energy getting warmer or colder over time?'
        accountability = 'Do not force a clean label onto a messy signal. Mixed intent is a real result, not a failure to detect something cleaner.'
    elif primary_label == 'playful_reengagement':
        diagnosis = 'There was confusion, then embarrassment, then the energy came back around — and that arc is actually more telling than if it had gone smoothly.'
        reasoning = 'The rough opening is not the story. What matters more is what happened after the confusion cleared: the tone shifted toward warmth and playfulness.'
        next_steps = 'Do not make this heavier than it is. Treat it like a slightly weird reconnection that both sides moved past.'
        accountability = 'The bigger risk is overthinking this into a problem it is not.'
    elif primary_label == 'light_sexual_reciprocity':
        diagnosis = 'There is real flirtatious energy here and it is being matched, not just tolerated.'
        reasoning = 'This is not just politeness. The other person is leaning in. The reciprocal tone is visible — no deflecting, no redirecting, no going cold.'
        next_steps = 'Stay with it. Let the energy breathe.'
        accountability = 'Do not talk yourself out of chemistry that is already working.'
    elif primary_label == 'warm_receptivity':
        diagnosis = 'The energy here is open and engaged — not guarded, not pulling back.'
        reasoning = 'What stands out is not intensity, it is the absence of resistance.'
        next_steps = 'Keep the tone easy and person-focused. Let consistency do the work from here.'
        accountability = 'Warm does not mean locked in. Do not skip the part where you actually build something.'
    elif primary_label == 'confusion_then_repair':
        diagnosis = 'It started awkward, but the repair happened — and that is the part that actually matters.'
        reasoning = 'People who are checked out do not bother repairing the energy.'
        next_steps = 'Do not drag the awkward moment back into the conversation. It already moved past — follow that lead.'
        accountability = 'Stop overanalyzing the confusion when the obvious read is simpler: embarrassment, recovery, still open.'
    elif primary_label == 'casual_flirtation':
        diagnosis = 'Light, easy, and going in the right direction.'
        reasoning = 'Nothing here is heavy or loaded. The tone is playful and the energy is positive.'
        next_steps = 'Keep it light. Do not make it heavier than it needs to be right now.'
        accountability = 'Not every good thing needs to be analyzed into the ground. Sometimes easy is just easy.'
    else:
        diagnosis = 'This is a real human interaction — low stakes, not a threat, just still early.'
        reasoning = 'Nothing here points to pressure, danger, or bad intent. It reads like a normal exchange between two people who are still figuring out the dynamic.'
        next_steps = 'Treat it lightly. Let the next few exchanges do the work instead of trying to force a conclusion from limited data.'
        accountability = 'Stop trying to solve it before it has had time to develop. You do not have enough information yet to make a hard call — and that is okay.'
    if relationship_type in {'dating','family','friend'}:
        accountability = accountability.rstrip('.') + ' — context for an established relationship, not a first impression.'
    out['presentation_mode']='connection'; out['mode_title']='Connection Analysis'
    out['mode_tagline']='Warm read on chemistry, receptivity, emotional movement, and what to do next.'
    out['human_label']=_human_label(primary_label,str(out.get('lane','')),str(out.get('domain_mode','')))
    out['diagnosis']=diagnosis; out['reasoning']=reasoning
    out['practical_next_steps']=next_steps; out['accountability']=accountability
    out['social_tone']=_social_tone(out); out['interest_summary']=_interest_summary(out)
    out['mode_override_note']=''
    return out
def interpret_analysis(result, extracted_text='', relationship_type='stranger', context_note='', requested_mode='risk'):
    out = dict(result or {})
    requested_mode = str(requested_mode or 'risk').lower().strip()
    if requested_mode not in {'connection','risk'}: requested_mode='risk'
    out['relationship_type'] = relationship_type
    if _risk_override(out):
        out = _risk_copy(out)
        if requested_mode == 'connection':
            out['mode_override_note'] = 'Connection mode was selected, but stronger safety signals pushed this result into a more protective read.'
        out['requested_mode'] = requested_mode
        return out
    if requested_mode == 'connection':
        out = _connection_copy(out, relationship_type=relationship_type)
    else:
        out = _risk_copy(out)
    out['requested_mode'] = requested_mode
    return out
def _risk_override(result):
    lane = str(result.get('lane','BENIGN'))
    risk_level = str(result.get('risk_level','LOW')).upper()
    return lane in {'FRAUD','COERCION_RISK'} or risk_level in {'HIGH','MEDIUM'}
def _risk_copy(out):
    lane = str(out.get('lane','BENIGN'))
    domain_mode = str(out.get('domain_mode','general_unknown'))
    if lane == 'FRAUD' and domain_mode == 'housing_rental':
        diagnosis='This looks more like a setup than a normal rental conversation.'
def _risk_override(result):
    lane = str(result.get('lane','BENIGN'))
    risk_level = str(result.get('risk_level','LOW')).upper()
    return lane in {'FRAUD','COERCION_RISK'} or risk_level in {'HIGH','MEDIUM'}
def _risk_copy(out):
    lane = str(out.get('lane','BENIGN'))
    domain_mode = str(out.get('domain_mode','general_unknown'))
    if lane == 'FRAUD' and domain_mode == 'housing_rental':
        diagnosis='This looks more like a setup than a normal rental conversation.'
        reasoning='The concern is the sequence. Once verification gets inverted, money enters the picture, the interaction stops reading like normal logistics.'
        next_steps='Slow it down immediately. Verify ownership and identity independently before you give money, documents, or trust.'
        accountability='Do not talk yourself out of obvious risk just because the tone sounds polite or routine.'
    elif lane == 'FRAUD':
        diagnosis='This reads more like a risk pattern than a normal interaction.'
        reasoning='What matters most is not one isolated line, but the overall pattern of pressure, extraction, contradiction, or control.'
        next_steps='Pause the interaction and verify independently before you give money, sensitive information, or control.'
        accountability='Do not explain away real risk signals just because the delivery feels smooth.'
    elif lane == 'COERCION_RISK':
        diagnosis='This is starting to feel like pressure, not just awkwardness.'
        reasoning='The visible pattern starts to lean on pressure or boundary friction, which matters more than tone alone.'
        next_steps='Tighten the boundary. State it clearly once, then watch whether the other person respects it.'
        accountability='Do not explain away pressure just because it arrives wrapped in charm, confusion, or emotion.'
    else:
        diagnosis='This does not currently read like a strong risk pattern, but the signals are not clean.'
        reasoning='Nothing here strongly supports fraud or coercion, but enough signals are present to warrant attention.'
        next_steps='Stay observant. Do not overreact, but do not ignore the signals that are present.'
        accountability='Do not manufacture danger — and do not dismiss real signals either.'
    out['presentation_mode']='risk'
    out['mode_title']='Risk Analysis'
    out['mode_tagline']='Sharper read on contradiction, pressure, extraction, and protective next steps.'
    out['human_label']=_human_label(str(out.get('primary_label','')),lane,domain_mode)
    out['diagnosis']=diagnosis
    out['reasoning']=reasoning
    out['practical_next_steps']=next_steps
    out['accountability']=accountability
    out['social_tone']='Not the focus here'
    out['interest_summary']='Not the focus here'
    out['mode_override_note']=''
    return out
def _connection_copy(out, relationship_type='stranger'):
    primary_label=str(out.get('primary_label','low_information_neutral'))
    connection_level=str(out.get('connection_level','')).upper()
    if connection_level=='NEGATIVE':
        diagnosis='The signals here are more resistant than receptive.'
        reasoning='There are active signals of discomfort, disengagement, or pushback.'
        next_steps='Give the conversation room to breathe. Pressing harder against clear resistance will not help.'
        accountability='Receptivity has to be present before chemistry can build. Do not talk yourself into a positive read when the signals point the other way.'
    elif connection_level=='MIXED_INTENT':
        diagnosis='There are positive and negative signals present at the same time — this one needs a careful read.'
        reasoning='The conversation contains both warmth and friction. That does not mean it is bad or good — the picture is genuinely mixed.'
        next_steps='Watch the direction of movement, not just the snapshot. Is the energy getting warmer or colder over time?'
        accountability='Do not force a clean label onto a messy signal. Mixed intent is a real result, not a failure to detect something cleaner.'
    elif primary_label=='casual_flirtation':
        diagnosis='Light, easy, and going in the right direction.'
        reasoning='Nothing here is heavy or loaded. The tone is playful and the energy is positive.'
        next_steps='Keep it light. Do not make it heavier than it needs to be right now.'
        accountability='Not every good thing needs to be analyzed into the ground. Sometimes easy is just easy.'
    else:
        diagnosis='This is a real human interaction — low stakes, not a threat, just still early.'
        reasoning='Nothing here points to pressure, danger, or bad intent. It reads like a normal exchange between two people still figuring out the dynamic.'
        next_steps='Treat it lightly. Let the next few exchanges do the work instead of forcing a conclusion from limited data.'
        accountability='Stop trying to solve it before it has had time to develop — and that is okay.'
    if relationship_type in {'dating','family','friend'}:
        accountability=accountability.rstrip('.')+' — context for an established relationship, not a first impression.'
    out['presentation_mode']='connection'
    out['mode_title']='Connection Analysis'
    out['mode_tagline']='Warm read on chemistry, receptivity, emotional movement, and what to do next.'
    out['human_label']=_human_label(primary_label,str(out.get('lane','')),str(out.get('domain_mode','')))
    out['diagnosis']=diagnosis
    out['reasoning']=reasoning
    out['practical_next_steps']=next_steps
    out['accountability']=accountability
    out['social_tone']=_social_tone(out)
    out['interest_summary']=_interest_summary(out)
    out['mode_override_note']=''
    return out
def interpret_analysis(result, extracted_text='', relationship_type='stranger', context_note='', requested_mode='risk'):
    out=dict(result or {})
    requested_mode=str(requested_mode or 'risk').lower().strip()
    if requested_mode not in {'connection','risk'}: requested_mode='risk'
    out['relationship_type']=relationship_type
    if _risk_override(out):
        out=_risk_copy(out)
        if requested_mode=='connection':
            out['mode_override_note']='Connection mode was selected, but stronger safety signals pushed this result into a more protective read.'
        out['requested_mode']=requested_mode
        return out
    if requested_mode=='connection':
        out=_connection_copy(out,relationship_type=relationship_type)
    else:
        out=_risk_copy(out)
    out['requested_mode']=requested_mode
    return out
