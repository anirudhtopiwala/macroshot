import { useEffect, useState, useRef, useCallback } from 'react';
import { Check, Camera, Trash2 } from '../components/icons';
import BackButton from '../components/BackButton';
import Button from '../components/Button';
import LoadingSpinner from '../components/LoadingSpinner';
import { api } from '../api/client';
import { hapticLight } from '../utils/haptics';
import { useToast } from '../components/Toast';
import { useAuth } from '../context/AuthContext';
import { getCached, setCache } from '../utils/apiCache';
import type { Profile, Prefs } from '../types';

const SEX_OPTIONS = [
  { value: 'male', label: 'Male' },
  { value: 'female', label: 'Female' },
  { value: 'not_specified', label: 'Prefer not to say' },
];

type CachedPersonal = { profile: Profile & { first_name?: string | null }; units: string };

export default function SettingsPersonal() {
  const { toast } = useToast();
  const { user, refetch: refetchAuth } = useAuth();
  const cached = getCached<CachedPersonal>('settings_personal');
  const [loading, setLoading] = useState(!cached);
  const [saving, setSaving] = useState(false);
  const [profile, setProfile] = useState<Profile & { first_name?: string | null }>(cached?.profile || {
    age: null, height_cm: null, weight_kg: null, sex: null,
    weight_goal_kg: null, activity_level: null, workouts_per_week: null,
    weight_change_rate_kg: null, goal: null,
  });
  const [name, setName] = useState(cached?.profile?.first_name || user?.first_name || '');
  const [avatarUrl, setAvatarUrl] = useState<string | null>(user?.avatar_url || null);
  const [uploading, setUploading] = useState(false);
  const [units, setUnits] = useState<string>(cached?.units || 'metric');
  const avatarInputRef = useRef<HTMLInputElement>(null);
  const savedRef = useRef<string>(cached ? JSON.stringify(cached.profile) : '');
  const savedNameRef = useRef<string>(cached?.profile?.first_name || user?.first_name || '');

  useEffect(() => {
    Promise.all([
      api.get<Profile & { first_name?: string | null }>('/settings/profile'),
      api.get<Prefs>('/settings/prefs'),
    ]).then(([p, prefs]) => {
      setProfile(p);
      setName(p.first_name || user?.first_name || '');
      setAvatarUrl(user?.avatar_url || null);
      setUnits(prefs.units_system || 'metric');
      savedRef.current = JSON.stringify(p);
      savedNameRef.current = p.first_name || user?.first_name || '';
      setCache('settings_personal', { profile: p, units: prefs.units_system || 'metric' });
    }).catch(() => {}).finally(() => setLoading(false));
  }, [user]);

  const dirty = JSON.stringify(profile) !== savedRef.current || name !== savedNameRef.current;

  const handleAvatarUpload = useCallback(async (file: File) => {
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await fetch('/macro_app/api/v1/settings/avatar', {
        method: 'POST',
        body: fd,
        credentials: 'include',
        headers: { 'X-Requested-With': 'MacroApp' },
      });
      if (!res.ok) throw new Error();
      const data = await res.json();
      setAvatarUrl(data.avatar_url + '?t=' + Date.now()); // Cache-bust
      hapticLight();
      toast('Photo updated!');
      // Refresh auth so Header picks up the new avatar
      refetchAuth();
    } catch {
      toast('Failed to upload photo', 'error');
    } finally {
      setUploading(false);
    }
  }, [toast, refetchAuth]);

  const handleAvatarRemove = useCallback(async () => {
    if (uploading) return;
    setUploading(true);
    try {
      await api.delete('/settings/avatar');
      setAvatarUrl(null);
      hapticLight();
      toast('Photo removed');
      refetchAuth();
    } catch {
      toast('Failed to remove photo', 'error');
    } finally {
      setUploading(false);
    }
  }, [uploading, toast, refetchAuth]);

  const handleSave = useCallback(async () => {
    if (profile.age != null) {
      if (profile.age < 16) {
        toast('Age must be above 16', 'error');
        return;
      }
      if (profile.age > 150) {
        toast('Age must be less than 150', 'error');
        return;
      }
    }
    setSaving(true);
    try {
      const data: Record<string, unknown> = { ...profile };
      if (name.trim()) data.first_name = name.trim();
      await api.put('/settings/profile', data);
      savedRef.current = JSON.stringify(profile);
      savedNameRef.current = name;
      setCache('settings_personal', { profile: { ...profile, first_name: name.trim() || undefined }, units });
      navigator.serviceWorker?.controller?.postMessage({ type: 'CLEAR_API_CACHE' });
      hapticLight();
      toast('Profile saved!');
    } catch {
      toast('Error saving profile', 'error');
    } finally {
      setSaving(false);
    }
  }, [profile, name, toast]);

  if (loading) {
    return <LoadingSpinner fullPage />;
  }

  const isImperial = units === 'imperial';
  const firstInitial = name?.trim()?.[0] || user?.first_name?.[0] || '';
  const lastInitial = user?.last_name?.[0] || '';
  const initials = (firstInitial + lastInitial) || user?.email?.[0] || '?';

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <BackButton fallbackPath="/settings" />
        <h1 className="text-lg font-bold">About You</h1>
      </div>

      <div className="glass-card p-6 space-y-5">
        {/* Avatar + Name */}
        <div className="flex flex-col items-center gap-3">
          <button
            onClick={() => avatarInputRef.current?.click()}
            className="relative group"
            disabled={uploading}
          >
            {avatarUrl ? (
              <img
                src={avatarUrl}
                alt=""
                referrerPolicy="no-referrer"
                className="w-20 h-20 rounded-full object-cover ring-2 ring-emerald-500/40"
              />
            ) : (
              <div className="w-20 h-20 rounded-full bg-emerald-600/30 border-2 border-emerald-500/40 flex items-center justify-center text-2xl font-bold text-emerald-400 uppercase">
                {initials}
              </div>
            )}
            <div className="absolute inset-0 rounded-full bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 group-active:opacity-100 transition-opacity">
              {uploading ? (
                <LoadingSpinner size="sm" />
              ) : (
                <Camera className="w-5 h-5 text-white" />
              )}
            </div>
            {/* Always-visible camera badge */}
            <div className="absolute -bottom-0.5 -right-0.5 w-7 h-7 rounded-full bg-emerald-500 border-2 flex items-center justify-center shadow-lg" style={{ borderColor: 'var(--bg-card)' }}>
              {uploading ? (
                <LoadingSpinner size="sm" />
              ) : (
                <Camera className="w-3.5 h-3.5 text-white" />
              )}
            </div>
          </button>
          <input
            ref={avatarInputRef}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) handleAvatarUpload(file);
              e.target.value = '';
            }}
          />
          {avatarUrl && (
            <button
              type="button"
              onClick={handleAvatarRemove}
              disabled={uploading}
              className="flex items-center gap-1 text-xs font-medium transition-opacity hover:opacity-70 disabled:opacity-40"
              style={{ color: 'var(--text-muted)' }}
            >
              <Trash2 className="w-3 h-3" />
              Remove photo
            </button>
          )}
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Your name"
            className="glass-input text-center text-lg font-semibold w-full max-w-[200px]"
          />
        </div>

        {/* Sex */}
        <div>
          <label className="text-xs mb-2 block" style={{ color: 'var(--text-secondary)' }}>Sex</label>
          <div className="flex gap-2">
            {SEX_OPTIONS.map((s) => (
              <button
                key={s.value}
                onClick={() => { hapticLight(); setProfile({ ...profile, sex: s.value }); }}
                className="flex-1 py-2.5 px-3 rounded-xl text-xs font-semibold transition-all duration-200"
                style={profile.sex === s.value ? {
                  background: 'rgba(16,185,129,0.12)',
                  border: '1px solid rgba(16,185,129,0.3)',
                  color: '#10b981',
                } : {
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border-glass)',
                  color: 'var(--text-muted)',
                }}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        {/* Age */}
        <div>
          <label className="text-xs mb-1 block" style={{ color: 'var(--text-secondary)' }}>Age</label>
          <input
            type="number"
            min={16}
            max={150}
            value={profile.age ?? ''}
            onChange={(e) => setProfile({ ...profile, age: e.target.value ? Number(e.target.value) : null })}
            placeholder="Age"
            className="glass-input w-full"
          />
        </div>

        {/* Height + Weight row */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              Height {isImperial ? '' : '(cm)'}
            </label>
            {isImperial ? (
              <div className="flex gap-2 mt-1">
                <div className="flex items-center gap-1 flex-1">
                  <input
                    type="number"
                    min={3}
                    max={7}
                    value={profile.height_cm ? Math.floor(profile.height_cm / 2.54 / 12) : ''}
                    onChange={(e) => {
                      const ft = Number(e.target.value);
                      const totalIn = profile.height_cm ? Math.round(profile.height_cm / 2.54) : 0;
                      const currentIn = totalIn % 12;
                      if (ft >= 3 && ft <= 7) {
                        setProfile({ ...profile, height_cm: Math.round((ft * 12 + currentIn) * 2.54) });
                      }
                    }}
                    className="glass-input w-full"
                    placeholder="5"
                  />
                  <span className="text-xs shrink-0" style={{ color: 'var(--text-muted)' }}>ft</span>
                </div>
                <div className="flex items-center gap-1 flex-1">
                  <input
                    type="number"
                    min={0}
                    max={11}
                    value={profile.height_cm ? Math.round(profile.height_cm / 2.54) % 12 : ''}
                    onChange={(e) => {
                      const inches = Number(e.target.value);
                      const totalIn = profile.height_cm ? Math.round(profile.height_cm / 2.54) : 0;
                      const currentFt = Math.floor(totalIn / 12);
                      if (inches >= 0 && inches <= 11) {
                        setProfile({ ...profile, height_cm: Math.round((currentFt * 12 + inches) * 2.54) });
                      }
                    }}
                    className="glass-input w-full"
                    placeholder="11"
                  />
                  <span className="text-xs shrink-0" style={{ color: 'var(--text-muted)' }}>in</span>
                </div>
              </div>
            ) : (
              <input
                type="number"
                min={120}
                max={215}
                value={profile.height_cm ? Math.round(profile.height_cm) : ''}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  if (v > 0) setProfile({ ...profile, height_cm: v });
                }}
                className="w-full glass-input mt-1"
              />
            )}
          </div>
          <div>
            <label className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              Weight {isImperial ? '(lbs)' : '(kg)'}
            </label>
            <div
              className="w-full glass-input mt-1 flex items-center justify-between cursor-default"
              style={{ color: 'var(--text-primary)' }}
            >
              <span>
                {profile.weight_kg
                  ? isImperial
                    ? Math.round(profile.weight_kg * 2.205)
                    : Math.round(profile.weight_kg * 10) / 10
                  : '-'}
              </span>
              <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>via log</span>
            </div>
          </div>
        </div>

        {/* Save */}
        <Button
          variant={dirty ? 'primary' : 'secondary'}
          className="w-full"
          onClick={handleSave}
          disabled={saving || !dirty}
        >
          {saving ? (
            <><LoadingSpinner size="sm" className="border-emerald-400" /> Saving...</>
          ) : dirty ? 'Save Changes' : (
            <><Check className="w-3.5 h-3.5" /> Saved</>
          )}
        </Button>
      </div>
    </div>
  );
}
