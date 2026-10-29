// painel-visao-geral.js — graficos da visao geral (painel/overview.html).
//
// Chart.js vem do npm, empacotado pelo Vite (antes: script do jsDelivr no template). So os
// pedacos usados sao registrados (barra + rosca), o que mantem o arquivo pequeno. Os dados chegam
// por <script type="application/json"> (|json_script) e as cores sao os tokens do tema
// (tokens.css): acompanham claro/escuro e a rosca usa a mesma cor dos selos (--st-<status>-texto).
import {
  ArcElement, BarController, BarElement, CategoryScale, Chart, DoughnutController, Legend,
  LinearScale, Tooltip,
} from 'chart.js'

Chart.register(ArcElement, BarController, BarElement, CategoryScale, DoughnutController, Legend, LinearScale, Tooltip)

const estiloRaiz = getComputedStyle(document.documentElement)
function token(nome) { return estiloRaiz.getPropertyValue(nome).trim() }
function dados(id) {
  const el = document.getElementById(id)
  return el ? JSON.parse(el.textContent) : []
}

Chart.defaults.color = token('--cor-texto-suave')
Chart.defaults.borderColor = token('--cor-borda')
Chart.defaults.font.family = token('--body-font') || 'sans-serif'
Chart.defaults.font.size = 13

const semana = document.getElementById('chartSemana')
if (semana) {
  new Chart(semana, {
    type: 'bar',
    data: {
      labels: dados('id_dias_semana'),
      datasets: [{
        label: 'Agendamentos',
        data: dados('id_dados_grafico_semana'),
        backgroundColor: token('--cor-marca-forte'),
        hoverBackgroundColor: token('--cor-marca-forte-hover'),
        borderRadius: 6,
        borderSkipped: false,
        maxBarThickness: 44,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, ticks: { stepSize: 1, precision: 0 } },
        x: { grid: { display: false } },
      },
    },
  })
}

const status = document.getElementById('chartStatus')
if (status) {
  new Chart(status, {
    type: 'doughnut',
    data: {
      labels: dados('id_status_labels'),
      datasets: [{
        data: dados('id_status_totais'),
        backgroundColor: dados('id_status_codigos').map(function (s) { return token('--st-' + s.toLowerCase() + '-texto') }),
        borderColor: token('--cor-superficie'),
        borderWidth: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '62%',
      plugins: { legend: { position: 'right', labels: { boxWidth: 12, padding: 10 } } },
    },
  })
}
