import React, { useState } from 'react';
import { supabase } from '../lib/supabase';
import { ShieldCheck, Lock, User, ArrowRight, Eye, EyeOff } from 'lucide-react';
import toast, { Toaster } from 'react-hot-toast';

function Login({ onLogin }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [pin, setPin] = useState(['', '', '', '']);
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [pendingProfile, setPendingProfile] = useState(null);

  const getSystemInfo = async () => {
    let location = "Unknown Location";
    let ip = "N/A";
    try {
      const res = await fetch('https://ipapi.co/json/');
      const data = await res.json();
      location = `${data.city}, ${data.region}, ${data.country_name}`;
      ip = data.ip;
    } catch (e) {}

    return {
      device: navigator.userAgent.substring(0, 100),
      location,
      ip
    };
  };

  const sendSecurityAlert = async (type, status, user) => {
    const info = await getSystemInfo();
    const message = `🚨 *SECURITY ALERT: ACQUISITION-AI*\n\n👤 *User:* ${user}\n📝 *Event:* ${type}\n📊 *Status:* ${status}\n📍 *Loc:* ${info.location}\n🌐 *IP:* ${info.ip}\n📱 *Device:* ${info.device}`;

    // Log to DB
    await supabase.from('user_logs').insert({
      username: user,
      event_type: type,
      status: status,
      device_info: info.device,
      location_info: info.location,
      ip_address: info.ip
    });

    // Send WhatsApp
    const waUrl = import.meta.env.VITE_WA_API_URL;
    const waId = import.meta.env.VITE_WA_INSTANCE_ID;
    const waToken = import.meta.env.VITE_WA_API_TOKEN;
    const waGroup = import.meta.env.VITE_WA_GROUP_ID;

    if (waUrl && waId && waToken && waGroup) {
      fetch(`${waUrl}/waInstance${waId}/sendMessage/${waToken}`, {
        method: 'POST',
        body: JSON.stringify({ chatId: waGroup, message }),
        headers: { 'Content-Type': 'application/json' }
      }).catch(() => {});
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);

    try {
      const { data: profile, error } = await supabase
        .from('profiles')
        .select('username, password, is_blocked, role')
        .eq('username', username)
        .maybeSingle();

      if (!profile) {
        toast.error('Username tidak ditemukan!');
        await sendSecurityAlert('Login Attempt', 'FAILED (User Not Found)', username);
        setLoading(false);
        return;
      }

      if (profile.password !== password) {
        toast.error('Password Salah!');
        await sendSecurityAlert('Login Attempt', 'FAILED (Wrong Password)', username);
        setLoading(false);
        return;
      }

      if (profile.is_blocked) {
        toast.error('AKSES DITOLAK: Akun Anda sedang diblokir.');
        await sendSecurityAlert('Login Attempt', 'DENIED (Blocked)', username);
        setLoading(false);
        return;
      }

      if (profile.role === 'admin') {
        setPendingProfile(profile);
        setLoading(false);
        toast('Security Check: Masukkan 4 digit PIN Admin', { icon: '🛡️' });
      } else {
        toast.success(`Selamat Datang, ${profile.username}`);
        await sendSecurityAlert('Login Success', 'SUCCESS (User Access)', profile.username);
        onLogin(profile);
      }

    } catch (err) {
      toast.error('Terjadi kesalahan pada sistem login');
      setLoading(false);
    }
  };

  const handlePinChange = (index, value) => {
    if (isNaN(value)) return;
    const newPin = [...pin];
    newPin[index] = value.substring(value.length - 1);
    setPin(newPin);

    // Auto-focus next input
    if (value && index < 3) {
      document.getElementById(`pin-${index + 1}`).focus();
    }
  };

  const handlePinVerify = async (e) => {
    e.preventDefault();
    const pinString = pin.join('');
    if (pinString.length < 4) return;

    setLoading(true);
    try {
      const { data: config } = await supabase
        .from('system_config')
        .select('value')
        .eq('key', 'admin_pin')
        .single();

      if (config && config.value === pinString) {
        toast.success('PIN Terverifikasi! Membuka Dashboard...');
        await sendSecurityAlert('Admin Access', 'SUCCESS (PIN Verified)', pendingProfile.username);
        onLogin(pendingProfile);
      } else {
        toast.error('PIN Keamanan Salah!');
        await sendSecurityAlert('Admin Access', 'FAILED (Wrong PIN)', pendingProfile.username);
        setPin(['', '', '', '']);
        document.getElementById('pin-0').focus();
      }
    } catch (err) {
      toast.error('Gagal verifikasi PIN database');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen w-full flex items-center justify-center bg-[#0b0d15] font-['Inter'] p-6">
      <Toaster position="top-right" />
      <div className="w-full max-w-[440px] space-y-10 animate-fade-in">
        <div className="text-center space-y-4">
          <div className="inline-flex p-4 bg-indigo-600/10 rounded-2xl border border-indigo-500/20 shadow-lg shadow-indigo-500/10">
            <ShieldCheck className="w-10 h-10 text-indigo-500" />
          </div>
          <div className="space-y-1">
            <h1 className="text-4xl font-black text-white italic tracking-tighter uppercase">AcquisitionAI</h1>
            <p className="text-slate-500 font-bold uppercase text-[10px] tracking-[0.3em]">Direct Access Portal</p>
          </div>
        </div>

        <div className="bg-slate-900/40 backdrop-blur-xl border border-white/5 p-10 rounded-[2.5rem] shadow-2xl relative overflow-hidden">
          {!pendingProfile ? (
            <form onSubmit={handleSubmit} className="space-y-8 relative z-10">
              <div className="space-y-6">
                <div className="space-y-3">
                  <label className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] ml-1">Username</label>
                  <div className="relative group">
                    <User className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500 group-focus-within:text-indigo-500 transition-colors" />
                    <input
                      type="text"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      className="w-full pl-12 pr-4 py-5 bg-white/[0.03] border-b-2 border-white/5 focus:border-indigo-500 transition-all outline-none text-xl font-bold text-white placeholder:text-slate-700"
                      placeholder="Enter Username"
                      required
                    />
                  </div>
                </div>

                <div className="space-y-3">
                  <label className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] ml-1">Security Password</label>
                  <div className="relative group">
                    <Lock className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500 group-focus-within:text-indigo-500 transition-colors" />
                    <input
                      type={showPassword ? "text" : "password"}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="w-full pl-12 pr-12 py-5 bg-white/[0.03] border-b-2 border-white/5 focus:border-indigo-500 transition-all outline-none text-xl font-bold text-white placeholder:text-slate-700"
                      placeholder="••••••••"
                      required
                    />
                    <button
                      type="button"
                      onClick={() => setShowPassword(!showPassword)}
                      className="absolute right-4 top-1/2 -translate-y-1/2 text-slate-500 hover:text-indigo-400 transition-colors"
                    >
                      {showPassword ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                    </button>
                  </div>
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-black py-5 rounded-2xl transition-all duration-300 shadow-xl shadow-indigo-600/20 active:scale-[0.98] disabled:opacity-70 flex items-center justify-center gap-3"
              >
                {loading ? "AUTHENTICATING..." : "LOGIN TO DASHBOARD"}
                <ArrowRight className="w-5 h-5" />
              </button>
            </form>
          ) : (
            <form onSubmit={handlePinVerify} className="space-y-8 relative z-10 animate-fade-in-up">
              <div className="text-center space-y-2">
                <h3 className="text-white font-black uppercase tracking-widest text-xs">Verify Admin PIN</h3>
                <p className="text-slate-500 text-[10px]">Enter the 4-digit security code from DB</p>
              </div>

              <div className="flex justify-center gap-4">
                {pin.map((digit, idx) => (
                  <input
                    key={idx}
                    id={`pin-${idx}`}
                    type="text"
                    maxLength={1}
                    value={digit}
                    onChange={(e) => handlePinChange(idx, e.target.value)}
                    className="w-14 h-16 bg-white/[0.05] border-2 border-white/10 rounded-xl text-center text-2xl font-black text-indigo-400 focus:border-indigo-500 outline-none transition-all"
                    autoFocus={idx === 0}
                  />
                ))}
              </div>

              <button
                type="submit"
                disabled={loading || pin.join('').length < 4}
                className="w-full bg-emerald-600 hover:bg-emerald-500 text-white font-black py-5 rounded-2xl transition-all duration-300 shadow-xl shadow-emerald-600/20 active:scale-[0.98] disabled:opacity-50"
              >
                {loading ? "VERIFYING PIN..." : "UNLOCK ACCESS"}
              </button>

              <button
                type="button"
                onClick={() => setPendingProfile(null)}
                className="w-full text-slate-600 hover:text-slate-400 text-[10px] font-black uppercase tracking-widest transition-colors"
              >
                ← Back to Login
              </button>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}

export default Login;
