import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { useAuthStore } from '../store/auth'
import type { ChatMessage, Meeting, Notice, Participant, SharedFile, User, WhiteboardState } from '../types'
const API_URL = (import.meta.env.VITE_API_URL || 'http://localhost:8000/api').replace(/\/$/, '')
export const api = axios.create({ baseURL: API_URL, timeout: 30_000 })
let refreshTask: Promise<string>|null = null
api.interceptors.request.use((config) => { const token=useAuthStore.getState().access; if(token) config.headers.Authorization=`Bearer ${token}`; return config })
api.interceptors.response.use((response)=>response, async (error:AxiosError) => {
  const original = error.config as InternalAxiosRequestConfig & {_retry?:boolean}
  if(error.response?.status===401 && original && !original._retry && !original.url?.includes('/auth/refresh/')) {
    const refresh=useAuthStore.getState().refresh
    if(!refresh) { useAuthStore.getState().clear(); throw error }
    original._retry=true
    try {
      if(!refreshTask) refreshTask=axios.post(`${API_URL}/auth/refresh/`,{refresh}).then(({data})=>{const s=useAuthStore.getState();if(s.user)s.setSession(data.access,data.refresh||refresh,s.user);return data.access}).finally(()=>{refreshTask=null})
      const access=await refreshTask;original.headers.Authorization=`Bearer ${access}`;return api(original)
    } catch { useAuthStore.getState().clear(); throw error }
  }
  throw error
})
export const authApi = {
  async login(username:string,password:string){const {data}=await api.post('/auth/login/',{username,password});return data as {access:string;refresh:string;user:User}},
  async register(form:{name:string;username:string;email:string;password:string}){const {data}=await api.post('/auth/register/',form);return data as {access:string;refresh:string;user:User}},
  async me(){const {data}=await api.get('/auth/me/');return data as User},
  async refreshSession(){const current=useAuthStore.getState();if(!current.refresh||!current.user)throw new Error('Your session has expired.');const {data}=await axios.post(`${API_URL}/auth/refresh/`,{refresh:current.refresh});current.setSession(data.access,data.refresh||current.refresh,current.user)},
  async logout(){const refresh=useAuthStore.getState().refresh;try{await api.post('/auth/logout/',{refresh})}finally{useAuthStore.getState().clear()}},
}
export const meetingApi = {
  async list(params?:Record<string,string>){const {data}=await api.get('/meetings/',{params});return data as Meeting[]},
  async create(body:Record<string,unknown>){const {data}=await api.post('/meetings/',body);return data as Meeting},
  async get(id:string){const {data}=await api.get(`/meetings/${id}/`);return data as Meeting},
  async lookup(code:string){const {data}=await api.get(`/meetings/lookup/${encodeURIComponent(code)}/`);return data as Meeting},
  async join(idOrCode:string,password?:string){const {data}=await api.post(`/meetings/${encodeURIComponent(idOrCode)}/join/`,{password});return data as {waiting:boolean;meeting:Meeting;participant?:Participant}},
  async joinStatus(id:string){const {data}=await api.get(`/meetings/${id}/join/status/`);return data as {state:string;meeting:Meeting}},
  async leave(id:string){await api.post(`/meetings/${id}/leave/`)},
  async end(id:string){await api.post(`/meetings/${id}/end/`)},
  async token(id:string){const {data}=await api.post(`/meetings/${id}/token/`);return data as {url:string;token:string;room:string}},
  async participants(id:string){const {data}=await api.get(`/meetings/${id}/participants/`);return data as Participant[]},
  async moderate(id:string,participant_id:number,action:string){const {data}=await api.post(`/meetings/${id}/participants/`,{participant_id,action});return data as Participant},
  async invite(id:string,emails:string[]){const {data}=await api.post(`/meetings/${id}/invite/`,{emails});return data as {link:string;detail:string;invited:number}},
  async cancel(id:string){await api.delete(`/meetings/${id}/`)},
}
export const collabApi={
  async messages(id:string){const {data}=await api.get(`/meetings/${id}/messages/`);return data as ChatMessage[]},
  async files(id:string){const {data}=await api.get(`/meetings/${id}/files/`);return data as SharedFile[]},
  async upload(id:string,file:File,onProgress?:(p:number)=>void){const fd=new FormData();fd.append('file',file);const {data}=await api.post(`/meetings/${id}/files/`,fd,{onUploadProgress:(e)=>{if(e.total)onProgress?.(Math.round(e.loaded/e.total*100))}});return data as SharedFile},
  async previewFile(id:number){const {data}=await api.get(`/files/${id}/`,{responseType:'blob'});return data as Blob},
  async download(id:number,name:string){const {data}=await api.get(`/files/${id}/`,{responseType:'blob'});const url=URL.createObjectURL(data);const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();window.setTimeout(()=>URL.revokeObjectURL(url),1000)},
  async deleteFile(id:number){await api.delete(`/files/${id}/`)},
  async board(id:string){const {data}=await api.get(`/meetings/${id}/whiteboard/`);return data.state as WhiteboardState},
  async saveBoard(id:string,state:WhiteboardState){await api.post(`/meetings/${id}/whiteboard/`,{state})},
}
export const userApi={
  async stats(){const {data}=await api.get('/stats/');return data as {hosted_meetings:number;completed_meetings:number;upcoming_meetings:number;minutes_hosted:number}},
  async notifications(){const {data}=await api.get('/notifications/');return data as {unread:number;results:Notice[]}},
  async markRead(id?:number){await api.patch(id?`/notifications/${id}/read/`:'/notifications/read-all/')},
  async updateProfile(body:FormData|Record<string,unknown>){const {data}=await api.patch('/auth/me/',body);return data as User},
  async changePassword(old_password:string,new_password:string){await api.post('/auth/change-password/',{old_password,new_password})},
  async forgotPassword(email:string){return api.post('/auth/forgot-password/',{email})},
  async resetPassword(uid:string,token:string,password:string){return api.post('/auth/reset-password/',{uid,token,password})},
}
export function meetingSocket(id:string,onEvent:(event:Record<string,unknown>)=>void){
  const access=useAuthStore.getState().access
  if(!access) throw new Error('Sign in to connect to this meeting.')
  const base=(import.meta.env.VITE_WS_URL || API_URL.replace(/^http/,'ws').replace(/\/api$/,'')).replace(/\/$/,'')
  const socket=new WebSocket(`${base}/ws/meetings/${id}/`,['connectx-auth',access])
  socket.onmessage=(event)=>{try{onEvent(JSON.parse(event.data) as Record<string,unknown>)}catch{/* Ignore malformed events. */}}
  return socket
}

