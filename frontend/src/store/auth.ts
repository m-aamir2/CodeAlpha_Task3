import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { User } from '../types'
interface AuthState { access: string|null; refresh: string|null; user: User|null; setSession: (access:string, refresh:string, user:User) => void; setUser: (user:User) => void; clear: () => void }
export const useAuthStore = create<AuthState>()(persist((set) => ({ access:null, refresh:null, user:null,
  setSession: (access, refresh, user) => set({access,refresh,user}), setUser: (user) => set({user}), clear: () => set({access:null,refresh:null,user:null}) }), {name:'connectx-session', partialize:(state) => ({access:state.access,refresh:state.refresh,user:state.user})}))

