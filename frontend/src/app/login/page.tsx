// File: frontend/src/app/login/page.tsx
'use client';

import React, { useEffect, useState } from 'react';
import { LogoMark } from '@/components/brand/Logo';
import { useAuth } from '@/contexts/AuthContext';
import { toAuthError } from '@/services/authApi';

type Step = 'password' | 'code';

const RESEND_COOLDOWN_SECONDS = 30;

const INPUT_CLASS =
  'w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-[15px] text-slate-900 transition-[border-color,box-shadow] duration-150 placeholder:text-slate-400 focus:border-accent focus:outline-none focus:ring-4 focus:ring-accent/15';
const PRIMARY_BUTTON_CLASS =
  'press w-full rounded-xl bg-accent px-3 py-2.5 text-[15px] font-semibold text-white shadow-xs hover:bg-accent-hover disabled:cursor-not-allowed disabled:opacity-40';

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
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-slate-50 px-4">
      {/* A faint dawn glow behind the card — the only color on the page. */}
      <div
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-[-30%] h-[70vh] w-[120vw] -translate-x-1/2 rounded-[50%] bg-[radial-gradient(closest-side,rgb(255_159_10/0.16),rgb(255_55_95/0.07)_55%,transparent)]"
      />
      <div className="relative w-full max-w-[380px]">
        <div className="mb-7 flex flex-col items-center text-center">
          <LogoMark className="h-14 w-14 drop-shadow-[0_6px_16px_rgb(255_80_60/0.3)]" />
          <p className="mt-4 text-[13px] font-semibold uppercase tracking-[0.08em] text-slate-500">First Light</p>
        </div>
        <div className="rounded-2xl border border-black/[0.06] bg-white/90 p-7 shadow-lg backdrop-blur-xl">

        {step === 'password' && (
          <form onSubmit={handlePasswordSubmit} className="space-y-4">
            <div className="mb-2 text-center">
              <h1 className="text-[22px] font-bold text-slate-900">Sign in</h1>
              <p className="mt-1 text-sm text-slate-500">Welcome back. Enter your details to continue.</p>
            </div>
            <div>
              <label htmlFor="email" className="mb-1.5 block text-[13px] font-medium text-slate-700">Email</label>
              <input
                id="email"
                type="email"
                required
                autoFocus
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={INPUT_CLASS}
              />
            </div>
            <div>
              <label htmlFor="password" className="mb-1.5 block text-[13px] font-medium text-slate-700">Password</label>
              <input
                id="password"
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={INPUT_CLASS}
              />
            </div>
            {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">{error}</p>}
            <button
              type="submit"
              disabled={busy}
              className={PRIMARY_BUTTON_CLASS}
            >
              {busy ? 'Checking…' : 'Continue'}
            </button>
          </form>
        )}

        {step === 'code' && (
          <form onSubmit={handleCodeSubmit} className="space-y-4">
            <div className="mb-2 text-center">
              <h1 className="text-[22px] font-bold text-slate-900">Enter your code</h1>
              <p className="mt-1 text-sm text-slate-500">
                A 6-digit code was emailed to <span className="font-medium text-slate-700">{email}</span>
                {expiresInMinutes != null && ` — it expires in ${expiresInMinutes} minutes.`}
              </p>
            </div>
            <div>
              <label htmlFor="code" className="mb-1.5 block text-[13px] font-medium text-slate-700">Code</label>
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
                className={`${INPUT_CLASS} text-center font-mono text-2xl font-semibold tracking-[0.5em]`}
              />
            </div>
            {notice && <p className="rounded-lg bg-emerald-50 px-3 py-2 text-xs text-emerald-700">{notice}</p>}
            {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">{error}</p>}
            <button
              type="submit"
              disabled={busy || code.length !== 6}
              className={PRIMARY_BUTTON_CLASS}
            >
              {busy ? 'Verifying…' : 'Sign in'}
            </button>
            <button
              type="button"
              onClick={handleResend}
              disabled={resending || resendCooldown > 0}
              className="w-full text-center text-[13px] font-medium text-accent hover:underline disabled:cursor-not-allowed disabled:text-slate-400 disabled:no-underline"
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
              className="w-full text-center text-[13px] text-slate-500 hover:text-slate-800"
            >
              Use a different account
            </button>
          </form>
        )}
        </div>
      </div>
    </div>
  );
}
