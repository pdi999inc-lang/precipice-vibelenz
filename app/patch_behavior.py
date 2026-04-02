import re

content = open("behavior.py").read()

methods = """
    def to_schema_dict(self):
        flags = self._active_flags()
        return {
            "risk_score":         round(self.pressure_score, 4),
            "flags":              flags,
            "confidence":         round(min(1.0, 0.55 + len(flags) * 0.07), 2),
            "degraded":           self.degraded,
            "pressure_score":     round(self.pressure_score, 4),
            "isolation_score":    0.0,
            "urgency_score":      0.0,
            "asymmetry_score":    0.0,
            "deterministic_flag": self.deterministic_flag,
        }

    def _active_flags(self):
        flags = []
        if self.pressure_score > 0.0:
            flags.append("pressure_present")
        if self.financial_mentions > 0:
            flags.append("financial_mention")
        if self.deterministic_flag:
            flags.append("deterministic_gate_triggered")
        return flags

"""

target = "\nclass BehaviorExtractor:"
content = content.replace(target, methods + target, 1)
open("behavior.py", "w").write(content)
print("patched ok")
