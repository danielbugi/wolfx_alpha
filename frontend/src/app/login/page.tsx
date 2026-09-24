// File: frontend/src/app/login/page.tsx
'use client';

import React, { useEffect, useState } from 'react';
import { ArrowTrendingUpIcon } from '@heroicons/react/24/outline';
import { useAuth } from '@/contexts/AuthContext';
import { toAuthError } from '@/services/authApi';

type Step = 'password' | 'code';

const RESEND_COOLDOWN_SECONDS = 30;

export default function LoginPage() {
  const { login, verifyCode } = useAuth();
  const [step, setStep] = useState<Step>('password');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [code, setCode] = useState('');
  const [challengeToken, setChallengeToken] = useState<string | null>(null);
  const [expiresInMinutes, setExpiresInMinutes] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);

  // Counts the resend cooldown down to 0, one second at a time, only while there's time left to count.
  useEffect(() => {
    if (resendCooldown <= 0) return;
    const timer = setTimeout(() => setResendCooldown((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [resendCooldown]);

  async function handlePasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result = await login(email, password);
      setChallengeToken(result.challengeToken);
      setExpiresInMinutes(result.expiresInMinutes);
      setStep('code');
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setError(toAuthError(err).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleResend() {
    setError(null);
    setNotice(null);
    setResending(true);
    try {
      const result = await login(email, password);
      setChallengeToken(result.challengeToken);
      setExpiresInMinutes(result.expiresInMinutes);
      setCode('');
      setNotice('A new code was sent.');
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setError(toAuthError(err).message);
    } finally {
      setResending(false);
    }
  }

  async function handleCodeSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!challengeToken) return;
    setError(null);
    setBusy(true);
    try {
      await verifyCode(challengeToken, code);
    } catch (err) {
      setError(toAuthError(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="mb-6 flex items-center gap-2">
          <ArrowTrendingUpIcon className="h-6 w-6 text-slate-700" />
          <span className="text-sm font-bold tracking-wide text-slate-800">Trading System</span>
        </div>

        {step === 'password' && (
          <form onSubmit={handlePasswordSubmit} className="space-y-4">
            <h1 className="text-sm font-semibold text-slate-800">Sign in</h1>
            <div>
              <label htmlFor="email" className="mb-1 block text-xs font-medium text-slate-600">Email</label>
              <input
                id="email"
                type="email"
                required
                autoFocus
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
              />
            </div>
            <div>
              <label htmlFor="password" className="mb-1 block text-xs font-medium text-slate-600">Password</label>
              <input
                id="password"
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-slate-500 focus:outline-none"
              />
            </div>
            {error && <p className="text-xs text-red-600">{error}</p>}
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-md bg-slate-800 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {busy ? 'Checking…' : 'Continue'}
            </button>
          </form>
        )}

        {step === 'code' && (
          <form onSubmit={handleCodeSubmit} className="space-y-4">
            <h1 className="text-sm font-semibold text-slate-800">Enter your code</h1>
            <p className="text-xs text-slate-500">
              A 6-digit code was emailed to {email}
              {expiresInMinutes != null && ` — it expires in ${expiresInMinutes} minutes.`}
            </p>
            <div>
              <label htmlFor="code" className="mb-1 block text-xs font-medium text-slate-600">Code</label>
              <input
                id="code"
                type="text"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                autoFocus
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                className="w-full rounded-md border border-slate-300 px-3 py-2 text-center text-lg tracking-[0.5em] focus:border-slate-500 focus:outline-none"
              />
            </div>
            {notice && <p className="text-xs text-emerald-600">{notice}</p>}
            {error && <p className="text-xs text-red-600">{error}</p>}
            <button
              type="submit"
              disabled={busy || code.length !== 6}
              className="w-full rounded-md bg-slate-800 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {busy ? 'Verifying…' : 'Sign in'}
            </button>
            <button
              type="button"
              onClick={handleResend}
              disabled={resending || resendCooldown > 0}
              className="w-full text-center text-xs font-medium text-slate-600 hover:text-slate-800 disabled:cursor-not-allowed disabled:text-slate-400"
            >
              {resending ? 'Sending…' : resendCooldown > 0 ? `Resend code (${resendCooldown}s)` : 'Resend code'}
            </button>
            <button
              type="button"
              onClick={() => {
                setStep('password');
                setCode('');
                setError(null);
                setNotice(null);
              }}
              className="w-full text-center text-xs text-slate-500 hover:text-slate-700"
            >
              Use a different account
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
