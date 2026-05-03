from fastapi import APIRouter, Request, HTTPException
from backend.data_access import get_player_row
from backend.schemas.performance_request import PerformanceRequest
import pandas as pd
import numpy as np

router = APIRouter(tags=["Performance"])


# =========================================================
# Feature list MUST match training exactly
# =========================================================
FEATURE_ORDER = [
    "minutes_played",
    "matches_played",
    "goals",
    "assists",
    "passes",
    "shots",
    "tackles",
    "goals_per_match",
    "assists_per_match",
    "actions_per_90",
    "shot_accuracy",
    "pass_success_rate",
    "age",
    "is_young",
    "is_veteran",
    "is_starter",
    "full_season",
]


# =========================================================
# Feature engineering (FIXED, LOGIC PRESERVED)
# =========================================================
def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Per-match metrics
    df["goals_per_match"] = df["goals"] / df["matches_played"].replace(0, 1)
    df["assists_per_match"] = df["assists"] / df["matches_played"].replace(0, 1)

    # Activity intensity
    df["actions_per_90"] = (
        df["passes"] + df["shots"] + df["tackles"]
    ) / (df["minutes_played"] / 90).replace(0, 1)

    # Accuracy metrics (FIXED)
    df["shot_accuracy"] = df["goals"] / df["shots"].replace(0, np.nan)
    df["shot_accuracy"] = df["shot_accuracy"].fillna(0)

    # FIXED: this was previously always = 1.0
    df["pass_success_rate"] = df["passes"] / df["minutes_played"].replace(0, 1)

    # Player profile flags
    df["is_young"] = (df["age"] < 23).astype(int)
    df["is_veteran"] = (df["age"] > 30).astype(int)
    df["is_starter"] = (df["minutes_played"] > 900).astype(int)
    df["full_season"] = (df["matches_played"] >= 30).astype(int)

    return df[FEATURE_ORDER].fillna(0)


# =========================================================
# Feature importance explanation
# =========================================================
def get_feature_importance_explanation(model, feature_names):
    try:
        xgb_model = model.named_steps["model"] if hasattr(model, "named_steps") else model
        importance = xgb_model.feature_importances_

        df_imp = pd.DataFrame({
            "feature": feature_names,
            "importance": importance
        }).sort_values("importance", ascending=False).head(5)

        return {
            "top_features": {
                row["feature"]: round(float(row["importance"]), 4)
                for _, row in df_imp.iterrows()
            },
            "explanation": "Top factors influencing the prediction"
        }

    except Exception:
        return {
            "top_features": {},
            "explanation": "Feature importance unavailable"
        }


# =========================================================
# Routes
# =========================================================
@router.get("/players")
def get_players(request: Request):
    df = request.app.state.dataset
    return sorted(df["player_name"].dropna().unique().tolist())


@router.post("/predict")
def predict_performance(payload: PerformanceRequest, request: Request):
    df = request.app.state.dataset
    model = request.app.state.performance_model

    player_name = payload.player_name
    player_row = get_player_row(player_name)

    if player_row is None:
        raise HTTPException(status_code=404, detail=f"Player '{player_name}' not found")

    # ---------------------------
    # Player prediction
    # ---------------------------
    player_df = pd.DataFrame([player_row])
    X_player = engineer_features(player_df)

    try:
        raw_prediction = float(model.predict(X_player)[0])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prediction error: {str(e)}")

    # ---------------------------
    # Normalize score (UNCHANGED LOGIC)
    # ---------------------------
    all_players = engineer_features(df)
    all_raw_preds = model.predict(all_players)

    min_score = all_raw_preds.min()
    max_score = all_raw_preds.max()

    normalized_score = 10 * (raw_prediction - min_score) / (max_score - min_score)
    normalized_score = float(np.clip(normalized_score, 0, 10))

    explanation = get_feature_importance_explanation(model, FEATURE_ORDER)

    return {
        "player": player_name,
        "predicted_performance": round(normalized_score, 2),
        "raw_model_score": round(raw_prediction, 3),
        "explanation": explanation
    }
