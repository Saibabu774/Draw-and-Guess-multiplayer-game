import React from 'react'
import { createRoot } from 'react-dom/client'
import { CssBaseline, ThemeProvider, createTheme } from '@mui/material'
import App from './App'
import './style.css'
createRoot(document.getElementById('root')!).render(<React.StrictMode><ThemeProvider theme={createTheme({palette:{mode:'light',primary:{main:'#5b5bd6'}}})}><CssBaseline/><App/></ThemeProvider></React.StrictMode>)
