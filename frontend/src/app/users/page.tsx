// File: frontend/src/app/users/page.tsx
'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { authApi, AuthUser, SessionInfo, UserRole, toAuthError } from '@/services/authApi';

function fmt(value: string | null): string {
  if (!value) return '—';
  return new Date(value).toLocaleString();
}

function SessionsRow({ userId }: { userId: number }) {
  const [sessions, setSessions] = useState<SessionInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    authApi.listUserSessions(userId).then(setSessions).catch((err) => setError(toAuthError(err).message));
  }, [userId]);

  useEffect(() => {
    load();
  }, [load]);

  async function revoke(sessionId: number) {
    try {
      await authApi.revokeUserSession(userId, sessionId);
      load();
    } catch (err) {
      setError(toAuthError(err).message);
    }
  }

  if (error) return <p className="text-xs text-red-600">{error}</p>;
  if (sessions === null) return <p className="text-xs text-slate-400">Loading sessions…</p>;
  if (sessions.length === 0) return <p className="text-xs text-slate-400">No sessions.</p>;

  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-slate-400">
          <th className="py-1 pr-3 font-medium">Created</th>
          <th className="py-1 pr-3 font-medium">Last used</th>
          <th className="py-1 pr-3 font-medium">Device</th>
          <th className="py-1 pr-3 font-medium">Status</th>
          <th className="py-1" />
        </tr>
      </thead>
      <tbody>
        {sessions.map((s) => (
          <tr key={s.id} className="border-t border-slate-100">
            <td className="py-1.5 pr-3 text-slate-600">{fmt(s.created_at)}</td>
            <td className="py-1.5 pr-3 text-slate-600">{fmt(s.last_used_at)}</td>
            <td className="max-w-[16rem] truncate py-1.5 pr-3 text-slate-500" title={s.user_agent ?? undefined}>{s.user_agent ?? '—'}</td>
            <td className="py-1.5 pr-3">
              {s.revoked_at ? <span className="text-slate-400">Revoked</span> : <span className="text-emerald-600">Active</span>}
            </td>
            <td className="py-1.5 text-right">
              {!s.revoked_at && (
                <button type="button" onClick={() => revoke(s.id)} className="text-red-600 hover:underline">
                  Sign out
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CreateUserForm({ onCreated }: { onCreated: () => void }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<UserRole>('collaborator');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await authApi.createUser(email, password, role);
      setEmail('');
      setPassword('');
      setRole('collaborator');
      onCreated();
    } catch (err) {
      setError(toAuthError(err).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 bg-white p-4">
      <div>
        <label htmlFor="new-email" className="mb-1 block text-xs font-medium text-slate-600">Email</label>
        <input
          id="new-email"
          type="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-56 rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none"
        />
      </div>
      <div>
        <label htmlFor="new-password" className="mb-1 block text-xs font-medium text-slate-600">Temporary password</label>
        <input
          id="new-password"
          type="text"
          required
          minLength={8}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-48 rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none"
        />
      </div>
      <div>
        <label htmlFor="new-role" className="mb-1 block text-xs font-medium text-slate-600">Role</label>
        <select
          id="new-role"
          value={role}
          onChange={(e) => setRole(e.target.value as UserRole)}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm focus:border-slate-500 focus:outline-none"
        >
          <option value="collaborator">Collaborator</option>
          <option value="owner">Owner</option>
        </select>
      </div>
      <button
        type="submit"
        disabled={busy}
        className="rounded-md bg-slate-800 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
      >
        {busy ? 'Creating…' : 'Create user'}
      </button>
      {error && <p className="w-full text-xs text-red-600">{error}</p>}
    </form>
  );
}

export default function UsersPage() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState<AuthUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);

  const load = useCallback(() => {
    authApi.listUsers().then(setUsers).catch((err) => setError(toAuthError(err).message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (currentUser && currentUser.role !== 'owner') {
    return (
      <div className="p-6">
        <p className="text-sm text-slate-500">This page is Owner-only.</p>
      </div>
    );
  }

  async function toggleActive(target: AuthUser) {
    try {
      if (target.is_active) {
        await authApi.deactivateUser(target.id);
      } else {
        await authApi.reactivateUser(target.id);
      }
      load();
    } catch (err) {
      setError(toAuthError(err).message);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div>
        <h1 className="text-lg font-semibold text-slate-800">Users</h1>
        <p className="text-sm text-slate-500">Accounts that may sign in to this dashboard, and their active sessions.</p>
      </div>

      <CreateUserForm onCreated={load} />

      {error && <p className="text-xs text-red-600">{error}</p>}

      {users === null ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : (
        <div className="space-y-3">
          {users.map((u) => (
            <div key={u.id} className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-medium text-slate-800">
                    {u.email} <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-500">{u.role}</span>
                  </p>
                  <p className="text-xs text-slate-400">
                    Created {fmt(u.created_at)} · Last sign-in {fmt(u.last_login_at)}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  {!u.is_active && <span className="text-xs font-medium text-slate-400">Deactivated</span>}
                  {u.id !== currentUser?.id && (
                    <button
                      type="button"
                      onClick={() => toggleActive(u)}
                      className="text-xs font-medium text-red-600 hover:underline"
                    >
                      {u.is_active ? 'Deactivate' : 'Reactivate'}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => setExpanded(expanded === u.id ? null : u.id)}
                    className="text-xs font-medium text-slate-500 hover:underline"
                  >
                    {expanded === u.id ? 'Hide sessions' : 'Sessions'}
                  </button>
                </div>
              </div>
              {expanded === u.id && (
                <div className="mt-3 border-t border-slate-100 pt-3">
                  <SessionsRow userId={u.id} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
