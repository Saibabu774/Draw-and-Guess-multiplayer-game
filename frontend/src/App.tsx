import { useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { Alert, Avatar, Button, Card, CardContent, Chip, Container, Divider, Grid, List, ListItem, ListItemAvatar, ListItemText, Paper, Stack, TextField, Typography } from '@mui/material'

const API = 'http://localhost:8000'
type Player = {id:number;display_name:string;score:number;ready:boolean;connected:boolean}
type Room = {room_code:string;status:string;current_round:number;total_rounds:number;drawer_id:number|null;players:Player[];time_left:number;word_hint:string|null}
type EventMsg = {event:string;data:any}
export default function App() {
  const [name,setName] = useState('Player')
  const [code,setCode] = useState('')
  const [room,setRoom] = useState<Room|null>(null)
  const [playerId,setPlayerId] = useState<number|null>(null)
  const [socketStatus,setSocketStatus] = useState('Disconnected')
  const [word,setWord] = useState('')
  const [guess,setGuess] = useState('')
  const [messages,setMessages] = useState<string[]>([])
  const [error,setError] = useState('')
  const [seconds,setSeconds] = useState(60)
  const [tool,setTool] = useState<'brush'|'eraser'>('brush')
  const [brush,setBrush] = useState(4)
  const [color,setColor] = useState('#202020')
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const socketRef = useRef<WebSocket|null>(null)
  const drawing = useRef(false)
  const last = useRef<{x:number;y:number}|null>(null)
  const isDrawer = room?.drawer_id === playerId

  const addMessage = (s:string) => setMessages(m => [...m.slice(-30),s])
  const send = (event:string,data:any={}) => { if(socketRef.current?.readyState===WebSocket.OPEN) socketRef.current.send(JSON.stringify({event,data})) }
  const connect = (roomCode:string,pid:number) => {
    socketRef.current?.close()
    const ws = new WebSocket(`ws://localhost:8000/ws/${roomCode}/${pid}`)
    socketRef.current = ws
    ws.onopen = () => setSocketStatus('Connected')
    ws.onclose = () => setSocketStatus('Disconnected')
    ws.onerror = () => setError('WebSocket connection failed. Is the backend running?')
    ws.onmessage = (e) => {
      const msg:EventMsg = JSON.parse(e.data); const d = msg.data
      if(msg.event==='lobby_updated') setRoom(d)
      if(msg.event==='secret_word') setWord(d.word)
      if(msg.event==='round_started') { setWord(''); setSeconds(d.seconds); setRoom(r=>r?{...r,status:'playing',current_round:d.round_number,drawer_id:d.drawer_id,word_hint:d.word_hint}:r); addMessage(`Round ${d.round_number}: ${d.drawer_name} is drawing`); clearLocalCanvas() }
      if(msg.event==='timer_updated') setSeconds(d.seconds)
      if(msg.event==='draw_stroke') drawStroke(d)
      if(msg.event==='canvas_cleared') clearLocalCanvas()
      if(msg.event==='guess_message') addMessage(`${d.display_name}: ${d.text}`)
      if(msg.event==='correct_guess') addMessage(`${d.display_name} guessed correctly! +${d.points} points`)
      if(msg.event==='score_updated') setRoom(r=>r?{...r,players:r.players.map(p=>{const q=d.players.find((x:any)=>x.id===p.id);return q?{...p,score:q.score}:p})}:r)
      if(msg.event==='round_ended') { setWord(d.word); setRoom(r=>r?{...r,status:'round_result'}:r); addMessage(`Round ended. The word was "${d.word}"`) }
      if(msg.event==='game_completed') { setRoom(r=>r?{...r,status:'completed'}:r); addMessage('Game completed! Final leaderboard is ready.') }
      if(msg.event==='player_disconnected') addMessage(`${d.display_name} disconnected`)
      if(msg.event==='error') setError(d.message)
    }
  }
  useEffect(()=>()=>socketRef.current?.close(),[])
  async function createRoom() {
    setError('')
    try { const res=await axios.post(`${API}/api/rooms`,{display_name:name,total_rounds:3}); setRoom(res.data.room);setCode(res.data.room_code);setPlayerId(res.data.player_id);connect(res.data.room_code,res.data.player_id) }
    catch(e:any){setError(e.response?.data?.detail||'Could not create room')}
  }
  async function joinRoom() {
    setError('')
    try { const res=await axios.post(`${API}/api/rooms/${code.toUpperCase()}/join`,{display_name:name});setRoom(res.data.room);setCode(res.data.room_code);setPlayerId(res.data.player_id);connect(res.data.room_code,res.data.player_id) }
    catch(e:any){setError(e.response?.data?.detail||'Could not join room')}
  }
  function clearLocalCanvas() { const c=canvasRef.current;if(c){const ctx=c.getContext('2d');ctx?.clearRect(0,0,c.width,c.height)} }
  function drawStroke(d:any) { const c=canvasRef.current;if(!c)return;const ctx=c.getContext('2d');if(!ctx)return;ctx.beginPath();ctx.moveTo(d.x1*c.width,d.y1*c.height);ctx.lineTo(d.x2*c.width,d.y2*c.height);ctx.strokeStyle=d.tool==='eraser'?'#ffffff':d.color;ctx.lineWidth=d.width;ctx.lineCap='round';ctx.stroke() }
  function point(e:React.PointerEvent<HTMLCanvasElement>) { const r=e.currentTarget.getBoundingClientRect();return{x:(e.clientX-r.left)/r.width,y:(e.clientY-r.top)/r.height} }
  function pointerDown(e:React.PointerEvent<HTMLCanvasElement>) { if(!isDrawer)return; drawing.current=true;last.current=point(e);e.currentTarget.setPointerCapture(e.pointerId) }
  function pointerMove(e:React.PointerEvent<HTMLCanvasElement>) { if(!drawing.current||!last.current||!isDrawer)return;const p=point(e);const stroke={x1:last.current.x,y1:last.current.y,x2:p.x,y2:p.y,color,width:brush,tool};drawStroke(stroke);send('draw_stroke',stroke);last.current=p }
  function pointerUp() { drawing.current=false;last.current=null }
  function submitGuess(e:React.FormEvent) {e.preventDefault();if(guess.trim()){send('guess',{text:guess});setGuess('')}}
  return <Container maxWidth="xl" sx={{py:4}}>
    <Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" alignItems={{sm:'center'}} mb={3} gap={2}>
      <div><Typography variant="h3" fontWeight={800}>🎨 Draw & Guess</Typography><Typography color="text.secondary">Real-time multiplayer drawing game</Typography></div>
      <Chip label={`WebSocket: ${socketStatus}`} color={socketStatus==='Connected'?'success':'default'}/>
    </Stack>
    {error&&<Alert severity="error" sx={{mb:2}} onClose={()=>setError('')}>{error}</Alert>}
    {!room ? <Paper sx={{p:3,maxWidth:560,mx:'auto'}}><Typography variant="h5" mb={2}>Join or create a room</Typography><Stack gap={2}>
      <TextField label="Display name" value={name} onChange={e=>setName(e.target.value)} inputProps={{maxLength:40}}/>
      <Button variant="contained" onClick={createRoom} disabled={!name.trim()}>Create new room</Button>
      <Divider>OR JOIN EXISTING ROOM</Divider>
      <TextField label="Room code" value={code} onChange={e=>setCode(e.target.value.toUpperCase())} inputProps={{maxLength:8}}/>
      <Button variant="outlined" onClick={joinRoom} disabled={!name.trim()||!code.trim()}>Join room</Button>
    </Stack></Paper> : <>
      <Paper sx={{p:2,mb:2}}><Stack direction={{xs:'column',sm:'row'}} alignItems={{sm:'center'}} justifyContent="space-between" gap={1}>
        <Typography variant="h5">Room: <b>{room.room_code}</b></Typography><Stack direction="row" gap={1}><Chip label={`Players: ${room.players.length}`}/><Chip label={`Round ${room.current_round}/${room.total_rounds}`}/><Chip color={room.status==='playing'?'success':'default'} label={room.status}/></Stack>
      </Stack></Paper>
      {room.status==='lobby'&&<Paper sx={{p:2,mb:2}}><Typography variant="h6">Lobby</Typography><Typography color="text.secondary" mb={1}>Ready up. The room creator starts the game when everyone is ready.</Typography>
        <List>{room.players.map(p=><ListItem key={p.id}><ListItemAvatar><Avatar>{p.display_name[0]?.toUpperCase()}</Avatar></ListItemAvatar><ListItemText primary={p.display_name+(p.id===playerId?' (you)':'')} secondary={p.ready?'Ready':'Not ready'}/><Chip label={p.ready?'Ready':'Waiting'} color={p.ready?'success':'default'}/></ListItem>)}</List>
        <Stack direction="row" gap={1}><Button variant="outlined" onClick={()=>send('ready',{ready:true})}>I'm ready</Button><Button variant="contained" onClick={()=>send('start_game')}>Start game</Button></Stack>
      </Paper>}
      {room.status!=='lobby'&&<Grid container spacing={2}>
        <Grid item xs={12} md={8}><Paper sx={{p:2}}>
          <Stack direction="row" justifyContent="space-between" alignItems="center" mb={1}><Typography variant="h6">Shared canvas</Typography><Chip label={`${seconds}s`} color={seconds<10?'error':'primary'}/></Stack>
          <Typography mb={1}>{isDrawer?`Your secret word: ${word||'Waiting...'}`:room.status==='playing'?`Drawer: ${room.players.find(p=>p.id===room.drawer_id)?.display_name||'Player'} · ${room.word_hint||'Guess the word!'}`:room.status==='completed'?'Final results':'Round result'}</Typography>
          <canvas ref={canvasRef} width={1000} height={600} onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp} className={isDrawer?'canvas drawing':'canvas'}/>
          <Stack direction={{xs:'column',sm:'row'}} gap={1} mt={2} alignItems={{sm:'center'}}>
            <Button variant={tool==='brush'?'contained':'outlined'} onClick={()=>setTool('brush')} disabled={!isDrawer}>Brush</Button><Button variant={tool==='eraser'?'contained':'outlined'} onClick={()=>setTool('eraser')} disabled={!isDrawer}>Eraser</Button>
            <TextField type="number" label="Brush size" size="small" value={brush} onChange={e=>setBrush(Math.max(1,Math.min(30,Number(e.target.value))))} sx={{width:130}} disabled={!isDrawer}/>
            <input aria-label="Brush color" type="color" value={color} onChange={e=>setColor(e.target.value)} disabled={!isDrawer}/>
            <Button color="error" onClick={()=>send('clear_canvas')} disabled={!isDrawer}>Clear canvas</Button>
          </Stack>
          {!isDrawer&&room.status==='playing'&&<form onSubmit={submitGuess}><Stack direction="row" gap={1} mt={2}><TextField fullWidth size="small" label="Your guess" value={guess} onChange={e=>setGuess(e.target.value)} inputProps={{maxLength:120}}/><Button type="submit" variant="contained">Guess</Button></Stack></form>}
        </Paper></Grid>
        <Grid item xs={12} md={4}><Stack gap={2}>
          <Paper sx={{p:2}}><Typography variant="h6">Scoreboard</Typography>{[...room.players].sort((a,b)=>b.score-a.score).map((p,i)=><Stack key={p.id} direction="row" alignItems="center" justifyContent="space-between" py={1}><Typography>{i+1}. {p.display_name}{p.id===room.drawer_id?' ✏️':''}</Typography><Typography fontWeight={700}>{p.score}</Typography></Stack>)}</Paper>
          <Paper sx={{p:2}}><Typography variant="h6">Game messages</Typography><List dense sx={{maxHeight:300,overflow:'auto'}}>{messages.slice().reverse().map((m,i)=><ListItem key={i}><ListItemText primary={m}/></ListItem>)}</List></Paper>
          {room.status==='completed'&&<Paper sx={{p:2}}><Typography variant="h6">Final leaderboard</Typography>{[...room.players].sort((a,b)=>b.score-a.score).map((p,i)=><Typography key={p.id}>{i+1}. {p.display_name} — {p.score} points</Typography>)}</Paper>}
        </Stack></Grid>
      </Grid>}
    </>}
    <Typography variant="caption" display="block" textAlign="center" mt={4} color="text.secondary">Development starter · API docs at http://localhost:8000/docs</Typography>
  </Container>
}
