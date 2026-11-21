import os
import joblib
import numpy as np
from sklearn.metrics import accuracy_score
from sklearn.model_selection import train_test_split
import xgboost as xgb


class TradingAIEngine:
    def __init__(self, symbol: str, timeframe: str = 'H1'):
        self.symbol = symbol
        self.timeframe = timeframe
        self.model_path = f'trading_xgb_model_{symbol}_{timeframe}.pkl'
        self.model = None
        self.feature_columns = [
            'rsi',
            'atr',
            'price_change',
            'body_size',
            'candle_range',
            'trend_direction',
            'div_signal',
            'div_strength',
            'ob_proximity',
        ]

        if os.path.exists(self.model_path):
            try:
                self.model = joblib.load(self.model_path)
            except Exception:
                self.model = None

    def train(self, df, min_accuracy: float = 0.52) -> dict:
        """آموزش آفلاین مدل با گیت کیفیت پیش از ذخیره"""
        missing = [c for c in self.feature_columns if c not in df.columns]
        if missing:
            return {'saved': False, 'reason': f'ستون‌های ناموجود: {missing}'}

        X = df[self.feature_columns].astype(float)
        y = df['target_label'].astype(int)

        if len(df) < 150 or y.nunique() < 2:
            return {'saved': False, 'reason': 'تعداد نمونه‌ها یا تنوع برچسب‌ها برای آموزش کافی نیست.'}

        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, shuffle=False
        )

        model = xgb.XGBClassifier(
            n_estimators=150,
            learning_rate=0.04,
            max_depth=4,
            subsample=0.8,
            colsample_bytree=0.8,
            random_state=42,
            eval_metric='logloss',
        )

        model.fit(X_train, y_train)
        preds = model.predict(X_test)
        acc = float(accuracy_score(y_test, preds))

        saved = False
        if acc >= min_accuracy:
            self.model = model
            joblib.dump(self.model, self.model_path)
            saved = True

        return {
            'saved': saved,
            'test_accuracy': round(acc, 4),
            'train_samples': len(X_train),
            'test_samples': len(X_test),
        }

    def score_setup(self, features_dict: dict) -> float:
        """
        محاسبه احتمال موفقیت ستاپ (بین 0.0 تا 1.0).
        اگر مدل هنوز آموزش نداده باشد، امتیاز خنثی 0.50 برمی‌گرداند.
        """
        if not self.model:
            return 0.50

        row = [[float(features_dict.get(col, 0.0)) for col in self.feature_columns]]
        probs = self.model.predict_proba(row)[0]
        return float(probs[1]) if len(probs) > 1 else float(probs[0])