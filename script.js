const display = document.querySelector('#display');

let currentValue = '0';
let storedValue = null;
let operator = null;
let waitingForOperand = false;

function updateDisplay() {
  display.textContent = currentValue;
}

function inputNumber(number) {
  if (currentValue === 'Error') clearCalculator();
  if (waitingForOperand) {
    currentValue = number === '.' ? '0.' : number;
    waitingForOperand = false;
  } else if (number === '.' && currentValue.includes('.')) {
    return;
  } else if (currentValue === '0' && number !== '.') {
    currentValue = number;
  } else {
    currentValue += number;
  }
  updateDisplay();
}

function calculate(left, right, selectedOperator) {
  const first = Number(left);
  const second = Number(right);
  switch (selectedOperator) {
    case '+': return first + second;
    case '-': return first - second;
    case '*': return first * second;
    case '/': return second === 0 ? null : first / second;
    default: return second;
  }
}

function inputOperator(nextOperator) {
  if (currentValue === 'Error') return;
  if (operator && !waitingForOperand) {
    const result = calculate(storedValue, currentValue, operator);
    if (result === null) return showError();
    currentValue = String(result);
  }
  storedValue = currentValue;
  operator = nextOperator;
  waitingForOperand = true;
  updateDisplay();
}

function evaluate() {
  if (!operator || storedValue === null || currentValue === 'Error') return;
  const result = calculate(storedValue, currentValue, operator);
  if (result === null) return showError();
  currentValue = String(result);
  storedValue = null;
  operator = null;
  waitingForOperand = true;
  updateDisplay();
}

function showError() {
  currentValue = 'Error';
  storedValue = null;
  operator = null;
  waitingForOperand = true;
  updateDisplay();
}

function clearCalculator() {
  currentValue = '0';
  storedValue = null;
  operator = null;
  waitingForOperand = false;
  updateDisplay();
}

function backspace() {
  if (waitingForOperand || currentValue === 'Error') return;
  currentValue = currentValue.length > 1 ? currentValue.slice(0, -1) : '0';
  updateDisplay();
}

document.querySelector('.keypad').addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.number !== undefined) inputNumber(button.dataset.number);
  if (button.dataset.operator) inputOperator(button.dataset.operator);
  if (button.dataset.action === 'equals') evaluate();
  if (button.dataset.action === 'clear') clearCalculator();
  if (button.dataset.action === 'backspace') backspace();
});

document.addEventListener('keydown', (event) => {
  if (/^[0-9.]$/.test(event.key)) inputNumber(event.key);
  if (['+', '-', '*', '/'].includes(event.key)) inputOperator(event.key);
  if (event.key === 'Enter' || event.key === '=') evaluate();
  if (event.key === 'Escape') clearCalculator();
  if (event.key === 'Backspace') backspace();
});
