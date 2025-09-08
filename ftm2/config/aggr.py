import os

AGGR_PROFILES = {
  1:  {"OPEN_TH":40, "PUP_TH":0.60, "RV_BAND":[0.35,0.65], "REGIME_ALLOW":["trend"],
       "RISK_R":0.03, "COOLDOWN_S":900, "TP":[0.8,1.6,2.4], "SL_ATR":2.0, "MIN_NOTIONAL":25, "ALLOW_FLAT":False},
  2:  {"OPEN_TH":35, "PUP_TH":0.59, "RV_BAND":[0.30,0.70], "REGIME_ALLOW":["trend"],
       "RISK_R":0.04, "COOLDOWN_S":600, "TP":[0.9,1.8,2.7], "SL_ATR":1.9, "MIN_NOTIONAL":25, "ALLOW_FLAT":False},
  3:  {"OPEN_TH":30, "PUP_TH":0.585,"RV_BAND":[0.25,0.75], "REGIME_ALLOW":["trend","range"],
       "RISK_R":0.05, "COOLDOWN_S":480, "TP":[1.0,2.0,3.0], "SL_ATR":1.8, "MIN_NOTIONAL":20, "ALLOW_FLAT":False},
  4:  {"OPEN_TH":26, "PUP_TH":0.58, "RV_BAND":[0.22,0.78], "REGIME_ALLOW":["trend","range"],
       "RISK_R":0.06, "COOLDOWN_S":360, "TP":[1.0,2.0,3.0], "SL_ATR":1.7, "MIN_NOTIONAL":20, "ALLOW_FLAT":False},
  5:  {"OPEN_TH":22, "PUP_TH":0.57, "RV_BAND":[0.20,0.80], "REGIME_ALLOW":["trend","range"],
       "RISK_R":0.08, "COOLDOWN_S":300, "TP":[1.0,2.0,3.0], "SL_ATR":1.6, "MIN_NOTIONAL":15, "ALLOW_FLAT":False},
  6:  {"OPEN_TH":18, "PUP_TH":0.56, "RV_BAND":[0.18,0.85], "REGIME_ALLOW":["trend","range"],
       "RISK_R":0.10, "COOLDOWN_S":240, "TP":[1.0,2.0,3.0], "SL_ATR":1.6, "MIN_NOTIONAL":10, "ALLOW_FLAT":False},
  7:  {"OPEN_TH":15, "PUP_TH":0.55, "RV_BAND":[0.15,0.88], "REGIME_ALLOW":["trend","range","flat"],
       "RISK_R":0.12, "COOLDOWN_S":180, "TP":[1.0,2.0,3.0], "SL_ATR":1.5, "MIN_NOTIONAL":10, "ALLOW_FLAT":True},
  8:  {"OPEN_TH":12, "PUP_TH":0.54, "RV_BAND":[0.12,0.90], "REGIME_ALLOW":["trend","range","flat"],
       "RISK_R":0.14, "COOLDOWN_S":120, "TP":[1.0,2.0,3.0], "SL_ATR":1.5, "MIN_NOTIONAL":10, "ALLOW_FLAT":True},
  9:  {"OPEN_TH":8,  "PUP_TH":0.53, "RV_BAND":[0.10,0.93], "REGIME_ALLOW":["trend","range","flat"],
       "RISK_R":0.16, "COOLDOWN_S":90,  "TP":[1.0,2.0,3.0], "SL_ATR":1.4, "MIN_NOTIONAL":10, "ALLOW_FLAT":True},
 10: {"OPEN_TH":5,  "PUP_TH":0.52, "RV_BAND":[0.08,0.95], "REGIME_ALLOW":["trend","range","flat"],
       "RISK_R":0.20, "COOLDOWN_S":60,  "TP":[1.0,2.0,3.0], "SL_ATR":1.4, "MIN_NOTIONAL":5,  "ALLOW_FLAT":True},
}

def load_aggr_level(state) -> int:
    # DB > ENV > default(6)
    lvl = None
    try:
        lvl = int(state.config.get("analysis.aggr_level"))
    except Exception:
        pass
    if lvl is None:
        lvl = int(os.getenv("AGGR_LEVEL", "6"))
    return max(1, min(10, lvl))

def load_aggr_profile(state) -> dict:
    lvl = load_aggr_level(state)
    prof = {**AGGR_PROFILES[lvl]}
    prof["LEVEL"] = lvl
    return prof
