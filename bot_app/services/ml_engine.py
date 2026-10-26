import os
import logging
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
import xgboost as xgb
import joblib  # برای ذخیره و بارگذاری مدل روی دیسک

logger = logging.getLogger(__name__)

class TradingAIEngine:

    def __init__(self, symbol, timeframe='5M'):
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
        ]

        # اگر از قبل مدلی ذخیره شده باشد، آن را بارگذاری می‌کنیم
        if os.path.exists(self.model_path):
            self.model = joblib.load(self.model_path)

    def train(self, df):
        """آموزش مدل با استفاده از دیتافریم حاوی ویژگی‌ها و برچسب‌ها"""
        # جدا کردن ویژگی‌ها (Features) و برچسب هدف (Label)
        X = df[self.feature_columns]
        y = df['target_label']  # 1 برای موفقیت/سود، 0 برای شکست

        # تقسیم داده‌ها به بخش آموزش (80%) و تست (20%)
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, shuffle=False
        )

        # ساخت مدل XGBoost Classifier
        self.model = xgb.XGBClassifier(
            n_estimators=100,
            learning_rate=0.05,
            max_depth=4,
            random_state=42,
            eval_metric='logloss',
        )

        # آموزش مدل
        self.model.fit(X_train, y_train)

        # ارزیابی عملکرد روی داده‌های تست
        y_pred = self.model.predict(X_test)
        acc = accuracy_score(y_test, y_pred)

        # ذخیره مدل روی هارد دیسک
        joblib.dump(self.model, self.model_path)
        return acc

    def predict_signal(self, latest_features_dict):
        """پیش‌بینی شرایط بازار برای معاملات دو طرفه (Long و Short)

        خروجی: (سیگنال: 'BUY', 'SELL' یا 'HOLD'، درصد اطمینان مدل)
        """
        if not self.model:
            return "HOLD", 0.0

        # آماده‌سازی داده
        input_data = [[latest_features_dict[col] for col in self.feature_columns]]

        # گرفتن احتمالات برای هر سه کلاس [کلاس 0: خنثی، کلاس 1: خرید، کلاس 2: فروش]
        probabilities = self.model.predict_proba(input_data)[0]

        # پیش‌بینی نهایی مدل (0، 1 یا 2)
        predicted_class = self.model.predict(input_data)[0]

        # کانفیدنسیِ واقعیِ همان تصمیمی که مدل گرفته است
        confidence = probabilities[predicted_class]

        # آستانه اطمینان مجاز (مثلاً 65 درصد)
        threshold = 0.65

        # تعیین نوع سیگنال بر اساس کلاس پیش‌بینی شده
        if predicted_class == 1 and confidence >= threshold:
            return "BUY", float(confidence)  # صعودی / لانگ

        elif predicted_class == 2 and confidence >= threshold:
            return "SELL", float(confidence)  # نزولی / شورت

        else:
            return "HOLD", float(confidence)  # اطمینان پایین یا بازار خنثی -> ورود ممنوع