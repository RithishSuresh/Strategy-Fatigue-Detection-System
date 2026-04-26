"""
ai_engine.py - Dual AI Engine: MLP Autoencoder + Isolation Forest
Combines technical indicators + ML anomaly detection to produce Fatigue Score 0-100.
"""
import numpy as np
import pandas as pd
import logging

from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from sklearn.preprocessing import StandardScaler
from sklearn.neural_network import MLPRegressor
from sklearn.ensemble import IsolationForest

logger = logging.getLogger(__name__)


class FatigueEngine:
    """
    Encapsulates the entire AI fatigue detection pipeline.
    Uses two models:
      1. MLP Autoencoder – detects sequence reconstruction error (acts like LSTM Autoencoder)
      2. Isolation Forest – detects point anomalies in feature space
    """

    def __init__(self):
        self.autoencoder = MLPRegressor(
            hidden_layer_sizes=(16, 8, 4, 8, 16),
            activation='relu',
            solver='adam',
            max_iter=300,
            random_state=42,
            warm_start=True
        )
        self.iso_forest = IsolationForest(
            n_estimators=100,
            contamination=0.1,
            random_state=42
        )
        self.scaler = StandardScaler()
        self.is_trained = False
        self.feature_names = ['returns', 'volatility', 'rsi_norm', 'macd_norm',
                              'bb_width', 'atr_norm', 'volume_spike', 'drawdown']

    def _extract_features(self, df: pd.DataFrame) -> np.ndarray | None:
        """Extract normalizable feature matrix from OHLCV DataFrame."""
        try:
            closes = df['close'].astype(float)
            highs = df['high'].astype(float)
            lows = df['low'].astype(float)
            vols = df['volume'].astype(float)

            if len(closes) < 20:
                return None

            returns = closes.pct_change().fillna(0)
            volatility = returns.rolling(10).std().fillna(0)
            rsi = RSIIndicator(closes, window=14).rsi().fillna(50) / 100.0
            macd_obj = MACD(closes)
            macd_line = macd_obj.macd().fillna(0)
            macd_norm = (macd_line - macd_line.mean()) / (macd_line.std() + 1e-9)
            bb = BollingerBands(closes, window=20)
            bb_width = ((bb.bollinger_hband() - bb.bollinger_lband()) / (closes + 1e-9)).fillna(0)
            atr = AverageTrueRange(highs, lows, closes, window=14).average_true_range().fillna(0)
            atr_norm = (atr / (closes + 1e-9)).fillna(0)
            vol_mean = vols.rolling(20).mean().fillna(vols.mean())
            vol_spike = (vols / (vol_mean + 1e-9)).clip(0, 5).fillna(1)
            roll_max = closes.cummax()
            drawdown = ((closes - roll_max) / (roll_max + 1e-9)).fillna(0)

            feat = pd.DataFrame({
                'returns': returns,
                'volatility': volatility,
                'rsi_norm': rsi,
                'macd_norm': macd_norm,
                'bb_width': bb_width,
                'atr_norm': atr_norm,
                'volume_spike': vol_spike,
                'drawdown': drawdown
            }).dropna()

            return feat.values
        except Exception as e:
            logger.error(f"Feature extraction error: {e}")
            return None

    def train(self, df: pd.DataFrame):
        """Train both models on historical data."""
        features = self._extract_features(df)
        if features is None or len(features) < 20:
            logger.warning("Insufficient data to train AI Engine.")
            return

        self.scaler.fit(features)
        scaled = self.scaler.transform(features)

        # Train Autoencoder (reconstructs its own input)
        self.autoencoder.fit(scaled, scaled)

        # Train Isolation Forest
        self.iso_forest.fit(scaled)

        self.is_trained = True
        logger.info(f"AI Engine trained on {len(features)} samples.")

    def predict(self, df: pd.DataFrame) -> tuple[float, str, str, dict]:
        """
        Returns: (score 0-100, status, explanation, components_dict)
        """
        if not self.is_trained or df is None or len(df) < 15:
            return 0.0, 'WARMING UP', 'AI Engine is warming up...', {}

        features = self._extract_features(df)
        if features is None or len(features) < 5:
            return 0.0, 'WARMING UP', 'Not enough feature data.', {}

        # Use latest window
        recent = features[-10:] if len(features) >= 10 else features
        scaled = self.scaler.transform(recent)

        # 1. Autoencoder reconstruction error
        pred = self.autoencoder.predict(scaled)
        mse = float(np.mean(np.power(scaled - pred, 2)))
        ae_score = min(mse * 4000, 100.0)

        # 2. Isolation Forest anomaly ratio
        iso_labels = self.iso_forest.predict(scaled)
        anomaly_ratio = list(iso_labels).count(-1) / len(iso_labels)
        iso_score = anomaly_ratio * 100.0

        # 3. Technical indicator scores
        closes = df['close'].astype(float)
        last_rsi = RSIIndicator(closes, window=14).rsi().iloc[-1]
        rsi_score = max(0, last_rsi - 70) * 2  # Penalise overbought

        # MACD momentum weakness
        macd_obj = MACD(closes)
        macd_hist = macd_obj.macd_diff().iloc[-1]
        macd_score = max(0, -macd_hist * 100)  # Negative histogram = weakness

        # Bollinger Band width (high = volatile)
        bb = BollingerBands(closes, window=20)
        bb_w = ((bb.bollinger_hband() - bb.bollinger_lband()) / closes).iloc[-1] * 100
        bb_score = min(bb_w * 10, 30.0)

        # Drawdown
        roll_max = closes.cummax().iloc[-1]
        drawdown = abs((closes.iloc[-1] - roll_max) / (roll_max + 1e-9)) * 100
        dd_score = min(drawdown * 3, 30.0)

        # Combine (weighted)
        final_score = (
            ae_score   * 0.35 +
            iso_score  * 0.25 +
            rsi_score  * 0.15 +
            macd_score * 0.10 +
            bb_score   * 0.10 +
            dd_score   * 0.05
        )
        final_score = round(min(final_score, 100.0), 2)

        # Status
        if final_score <= 30:
            status = 'HEALTHY'
            decision = 'BUY'
        elif final_score <= 60:
            status = 'WARNING'
            decision = 'HOLD'
        elif final_score <= 80:
            status = 'FATIGUED'
            decision = 'REDUCE'
        else:
            status = 'CRITICAL'
            decision = 'EXIT'

        # Explanation
        reasons = []
        if last_rsi > 70: reasons.append('RSI Overbought')
        if last_rsi < 30: reasons.append('RSI Oversold')
        if macd_hist < 0: reasons.append('Weak MACD Momentum')
        if bb_w > 3: reasons.append('High Bollinger Volatility')
        if drawdown > 5: reasons.append(f'Drawdown {drawdown:.1f}%')
        if anomaly_ratio > 0.3: reasons.append('IsoForest Anomaly Detected')
        if ae_score > 50: reasons.append('Autoencoder Sequence Break')
        explanation = ' | '.join(reasons) if reasons else 'Strategy performing normally'

        components = {
            'autoencoder_score': round(ae_score, 2),
            'isolation_score': round(iso_score, 2),
            'rsi': round(last_rsi, 2),
            'rsi_score': round(rsi_score, 2),
            'macd_hist': round(macd_hist, 4),
            'bb_width': round(bb_w, 4),
            'drawdown_pct': round(drawdown, 2),
            'decision': decision,
            'fatigue': final_score
        }

        return final_score, status, explanation, components


# Global singleton engine per symbol
_engines: dict[str, FatigueEngine] = {}


def get_engine(symbol: str) -> FatigueEngine:
    if symbol not in _engines:
        _engines[symbol] = FatigueEngine()
    return _engines[symbol]


def analyse(symbol: str, df: pd.DataFrame) -> dict:
    """Train (if needed) and return a full metrics dict."""
    engine = get_engine(symbol)
    if not engine.is_trained and len(df) >= 50:
        engine.train(df)
    score, status, explanation, components = engine.predict(df)
    return {
        'fatigue': score,
        'status': status,
        'explanation': explanation,
        'decision': components.get('decision', 'HOLD'),
        **components
    }


def generate_iot_sensors() -> dict:
    """Simulates live IoT virtual sensor readings."""
    import psutil
    try:
        cpu = psutil.cpu_percent(interval=0.1)
        ram = psutil.virtual_memory().percent
    except:
        cpu = round(np.random.uniform(15, 75), 1)
        ram = round(np.random.uniform(30, 70), 1)

    return {
        'cpu_usage': round(cpu, 1),
        'ram_usage': round(ram, 1),
        'api_latency': int(np.random.uniform(20, 180)),
        'network_delay': int(np.random.uniform(10, 100)),
        'trade_frequency': int(np.random.uniform(1, 15)),
        'orders_per_min': int(np.random.uniform(0, 20)),
        'risk_pressure': round(np.random.uniform(10, 90), 1),
        'heartbeat': 'OK'
    }
