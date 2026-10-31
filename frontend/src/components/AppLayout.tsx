import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Bell, CalendarDays, ChevronDown, CircleHelp, Headphones, LayoutDashboard, LogOut, Menu, Moon, Search, Settings, Sun, Video } from 'lucide-react'
import { authApi, userApi } from '../services/api'
import { useAuthStore } from '../store/auth'
import { Avatar, ToastHost } from './ui'
import { toast } from '../utils/ui'

export function AppLayout(){
  const [open,setOpen]=useState(false);const nav=useNavigate();const qc=useQueryClient();const storeUser=useAuthStore(s=>s.user);const access=useAuthStore(s=>s.access);const setUser=useAuthStore(s=>s.setUser)
  const [theme,setTheme]=useState<'light'|'dark'>(()=>localStorage.getItem('connectx-theme')==='dark'?'dark':'light')
  useEffect(()=>{document.documentElement.dataset.theme=theme;localStorage.setItem('connectx-theme',theme)},[theme])
  const me=useQuery({queryKey:['me'],queryFn:authApi.me});useEffect(()=>{if(me.data)setUser(me.data)},[me.data,setUser])
  useEffect(()=>{if(!access)return;const timer=window.setInterval(()=>{void authApi.refreshSession().catch(()=>useAuthStore.getState().clear())},10*60*1000);return()=>window.clearInterval(timer)},[access])
  const notices=useQuery({queryKey:['notifications'],queryFn:userApi.notifications,refetchInterval:30000})
  useEffect(()=>{if(!access)return;let socket:WebSocket|undefined;let alive=true;let retry:number|undefined;const connect=()=>{if(!alive)return;const api=(import.meta.env.VITE_API_URL||'http://localhost:8000/api').replace(/\/$/,'');const base=(import.meta.env.VITE_WS_URL||api.replace(/^http/,'ws').replace(/\/api$/,'')).replace(/\/$/,'');socket=new WebSocket(`${base}/ws/notifications/`,['connectx-auth',access]);socket.onmessage=e=>{try{const n=JSON.parse(e.data);void qc.invalidateQueries({queryKey:['notifications']});toast(n.title||'New notification','info')}catch{/* Ignore malformed events. */}};socket.onclose=()=>{if(alive)retry=window.setTimeout(connect,2500)}};connect();return()=>{alive=false;if(retry)window.clearTimeout(retry);socket?.close()}},[access,qc])
  const user=me.data||storeUser
  const signOut=async()=>{try{await authApi.logout()}finally{nav('/login')}}
  return <div className="app-shell"><aside className={`sidebar ${open?'sidebar-open':''}`}>
    <div className="brand"><span className="brand-mark"><Video size={19}/></span><span>connect<span className="brand-accent">x</span></span><button className="mobile-sidebar-close" onClick={()=>setOpen(false)}>×</button></div>
    <div className="workspace-switch"><span className="workspace-avatar">{(user?.name||'C')[0]}</span><span><b>Personal workspace</b><small>Free plan</small></span><ChevronDown size={15}/></div>
    <nav className="side-nav"><div className="nav-label">WORKSPACE</div><NavLink to="/app" end><LayoutDashboard/><span>Overview</span></NavLink><NavLink to="/app/meetings"><CalendarDays/><span>Meetings</span></NavLink><NavLink to="/app/notifications"><Bell/><span>Notifications</span>{(notices.data?.unread||0)>0&&<i className="nav-count">{notices.data?.unread}</i>}</NavLink><NavLink to="/app/settings"><Settings/><span>Settings</span></NavLink></nav>
    <div className="side-spacer"/><div className="sidebar-help"><div className="help-icon"><CircleHelp size={18}/></div><b>Need a hand?</b><span>Explore tips to get the best out of ConnectX.</span><button onClick={()=>window.open('https://docs.livekit.io/','_blank','noopener,noreferrer')}>Visit help center <span>↗</span></button></div>
    <div className="sidebar-user"><Avatar user={user} size="md"/><div><b>{user?.name||'Your account'}</b><span>{user?.email||'Loading profile…'}</span></div><button className="icon-btn" aria-label="Sign out" title="Sign out" onClick={signOut}><LogOut size={17}/></button></div>
  </aside><main className="main-column"><div className="topbar"><button className="mobile-menu icon-btn" onClick={()=>setOpen(true)}><Menu/></button><div className="breadcrumb"><span>Workspace</span><span className="crumb-slash">/</span><b>ConnectX</b></div><div className="topbar-right"><button className="search-trigger" onClick={()=>nav('/app/meetings')}><Search size={16}/><span>Search meetings</span><kbd>⌘ K</kbd></button><button className="icon-btn" aria-label={theme==='light'?'Switch to dark theme':'Switch to light theme'} title={theme==='light'?'Switch to dark theme':'Switch to light theme'} onClick={()=>setTheme(theme==='light'?'dark':'light')}>{theme==='light'?<Moon size={17}/>:<Sun size={17}/>}</button><button className="icon-btn" aria-label="Notifications" onClick={()=>nav('/app/notifications')}><Bell size={18}/>{(notices.data?.unread||0)>0&&<i className="bell-dot"/>}</button><span className="topbar-divider"/><button className="profile-trigger" onClick={()=>nav('/app/settings')}><Avatar user={user} size="sm"/><ChevronDown size={14}/></button></div></div><div className="page-content"><Outlet/></div><footer className="app-footer"><span>© 2026 ConnectX</span><span>Built for better conversations <span className="footer-heart">●</span></span><span><Headphones size={14}/> Help & support</span></footer></main><ToastHost/></div>
}

