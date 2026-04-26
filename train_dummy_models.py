import numpy as np
import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, RepeatVector, TimeDistributed, Input
import os

# Create models directory if it doesn't exist
if not os.path.exists("models"):
    os.makedirs("models")

# Define input shape
# Features: Returns, Volatility, Drawdown, Trend, Momentum -> 5 features
# We will use window of 10 time steps
TIME_STEPS = 10
FEATURES = 5

print("Building dummy LSTM model...")
# Dummy LSTM Model for prediction
lstm_model = Sequential([
    Input(shape=(TIME_STEPS, FEATURES)),
    LSTM(32, activation='relu'),
    Dense(16, activation='relu'),
    Dense(1, activation='linear') # Predict next step return/behavior
])
lstm_model.compile(optimizer='adam', loss='mse')

# Dummy data for compiling/saving without errors
X_dummy = np.random.rand(1, TIME_STEPS, FEATURES)
y_dummy = np.random.rand(1, 1)
lstm_model.fit(X_dummy, y_dummy, epochs=1, verbose=0)
lstm_model.save("models/lstm_model.h5")
print("Saved models/lstm_model.h5")

print("Building dummy Autoencoder model...")
# Dummy Autoencoder for Anomaly Detection
autoencoder = Sequential([
    Input(shape=(TIME_STEPS, FEATURES)),
    LSTM(16, activation='relu', return_sequences=False),
    RepeatVector(TIME_STEPS),
    LSTM(16, activation='relu', return_sequences=True),
    TimeDistributed(Dense(FEATURES))
])
autoencoder.compile(optimizer='adam', loss='mse')

# Dummy data for compiling/saving without errors
X_ae_dummy = np.random.rand(1, TIME_STEPS, FEATURES)
autoencoder.fit(X_ae_dummy, X_ae_dummy, epochs=1, verbose=0)
autoencoder.save("models/autoencoder.h5")
print("Saved models/autoencoder.h5")

print("Dummy models generated successfully.")
