import numpy as np
import torch
import torch.nn as nn

class KalmanFilter:
    def __init__(self, Q=1e-4, R=1e-2, P=1.0):
        self.Q = Q
        self.R = R
        self.P = P
        self.x = None  # hidden state L*
    def update(self, observation, Q_t, R_t):
        if self.x is None:
            self.x = observation
            self.P = 1.0
            return self.x, 0.0
        # Predict
        x_pred = self.x
        P_pred = self.P + Q_t
        # Update
        K = P_pred / (P_pred + R_t)
        innovation = observation - x_pred
        self.x = x_pred + K * innovation
        self.P = (1 - K) * P_pred
        return self.x, innovation

class QRPredictor(nn.Module):
    def __init__(self, input_dim=10):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 32),
            nn.ReLU(),
            nn.Linear(32, 32),
            nn.ReLU(),
            nn.Linear(32, 2)  # outputs Q and R (log variances)
        )
    def forward(self, x):
        out = self.net(x)
        # Ensure positive: use softplus
        return torch.nn.functional.softplus(out) + 1e-6