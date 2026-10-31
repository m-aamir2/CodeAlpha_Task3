import { lazy, Suspense } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router-dom'
import { AppLayout } from './components/AppLayout'
import { AuthPage, ForgotPasswordPage, ResetPasswordPage } from './pages/AuthPages'
import { DashboardPage, MeetingsPage, NotificationsPage, SettingsPage } from './pages/AppPages'
import { MeetingDetailPage } from './pages/MeetingDetailPage'
import { JoinPage } from './pages/JoinPage'
import { useAuthStore } from './store/auth'

const MeetingRoomPage=lazy(()=>import('./pages/MeetingRoomPage').then((module)=>({default:module.MeetingRoomPage})))
const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 15_000, refetchOnWindowFocus: false } } })
function Protected() { const access = useAuthStore((state) => state.access); return access ? <Outlet /> : <Navigate to="/login" replace /> }
function Application() {
  return <QueryClientProvider client={queryClient}><BrowserRouter><Routes>
    <Route path="/" element={<Navigate to={useAuthStore.getState().access ? '/app' : '/login'} replace />} />
    <Route path="/login" element={<AuthPage />} /><Route path="/register" element={<AuthPage register />} />
    <Route path="/forgot-password" element={<ForgotPasswordPage />} /><Route path="/reset-password" element={<ResetPasswordPage />} />
    <Route element={<Protected />}>
      <Route path="/app" element={<AppLayout />}><Route index element={<DashboardPage />} /><Route path="meetings" element={<MeetingsPage />} /><Route path="meetings/:id" element={<MeetingDetailPage />} /><Route path="notifications" element={<NotificationsPage />} /><Route path="settings" element={<SettingsPage />} /></Route>
      <Route path="/join/:code" element={<JoinPage />} /><Route path="/room/:id" element={<Suspense fallback={<div className="room-loading"><span className="spinner"/>Loading meeting tools…</div>}><MeetingRoomPage /></Suspense>} />
    </Route>
    <Route path="*" element={<Navigate to="/app" replace />} />
  </Routes></BrowserRouter></QueryClientProvider>
}
export default Application

