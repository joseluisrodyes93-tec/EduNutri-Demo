from flask import Flask, render_template, request, redirect, url_for, session
import mysql.connector
from datetime import datetime, timedelta
import os  # <-- Agregamos esta librería para leer las variables de entorno

app = Flask(__name__)
app.secret_key = 'bosco_play_secret_key_2026'

# --- CONFIGURACIÓN DE CONEXIÓN INTELIGENTE ---
def get_db_connection():
    return mysql.connector.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        port=int(os.environ.get('DB_PORT', 3306)),  # Usa el puerto de Aiven en la nube o 3306 en tu PC
        user=os.environ.get('DB_USER', 'root'),
        password=os.environ.get('DB_PASSWORD', ''), # Tu contraseña local vacía
        database=os.environ.get('DB_NAME', 'db_colegio_respaldo') # Tu base local por defecto
    )

@app.route('/')
def index():
    return render_template('login.html')

# --- LOGIN (CÉDULA O CORREO) ---
@app.route('/login', methods=['POST'])
def login():
    identificador = request.form['correo'] 
    password = request.form['password']
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    # Buscamos si coincide con el correo O con la cédula
    query = """
        SELECT id_app, cedula, nombre_completo, rol 
        FROM padres 
        WHERE (correo = %s OR cedula = %s) AND password = %s
    """
    cursor.execute(query, (identificador, identificador, password))
    user = cursor.fetchone()
    
    cursor.close()
    conn.close()
    
    if user:
        # Guardamos todo en la sesión
        session['user_id'] = user['id_app']
        session['cedula'] = user['cedula']
        session['user_name'] = user['nombre_completo']
        session['rol'] = user.get('rol', 'padre')  # Por defecto 'padre' si no hay rol
        
        # Redirección según el rol
        if session['rol'] == 'bar':
            return redirect(url_for('panel_bar'))
        else:
            return redirect(url_for('dashboard'))
    
    # IMPORTANTE: Si las credenciales fallan, DEBE haber un return aquí
    return "Error: Credenciales incorrectas. <a href='/'>Volver a intentar</a>"

# --- REGISTRO ---
@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        cedula = request.form['cedula']
        correo = request.form['correo']
        password = request.form['password']
        
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True, buffered=True) 
        
        try:
            cursor.execute("SELECT name FROM billing_informations WHERE idnumber = %s", (cedula,))
            datos_colegio = cursor.fetchone()
            
            if not datos_colegio:
                return "Error: Esta cédula no existe en los registros del colegio."

            query = "INSERT INTO padres (cedula, correo, password, nombre_completo, saldo) VALUES (%s, %s, %s, %s, 0.00)"
            cursor.execute(query, (cedula, correo, password, datos_colegio['name']))
            
            conn.commit()
            return """
            <script>
                alert('¡Usuario creado exitosamente!');
                window.location.href = '/';
            </script>
            """
        except mysql.connector.Error as err:
            return f"Error: {err}"
        finally:
            cursor.close()
            conn.close()
    return render_template('registro.html')

# --- DASHBOARD ---
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    
    cedula_padre = session['cedula']
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    # 1. Obtener saldo Y fecha_vencimiento del padre (AQUÍ ESTÁ EL CAMBIO)
    cursor.execute("SELECT saldo, fecha_vencimiento FROM padres WHERE id_app = %s", (session['user_id'],))
    padre_data = cursor.fetchone()
    
    saldo_padre = padre_data['saldo'] if padre_data else 0.0
    # Guardamos la fecha para usarla abajo
    vencimiento_padre = padre_data['fecha_vencimiento'] if padre_data else None

    # 2. Vínculo con hijos
    query_vinculo = """
        SELECT s.person_id as id_student, p.firstname, p.lastname
        FROM billing_informations bi
        JOIN students s ON bi.id = s.billing_information_id
        JOIN people p ON s.person_id = p.id
        WHERE TRIM(bi.idnumber) = TRIM(%s)
    """
    cursor.execute(query_vinculo, (cedula_padre,))
    hijos = cursor.fetchall()

    for hijo in hijos:
        hijo['nombre_hijo'] = f"{hijo['firstname']} {hijo['lastname']}"
        hijo['fecha_vencimiento'] = vencimiento_padre
        cursor.execute("""
            SELECT p.estado, m.nombre_item 
            FROM pedidos p 
            JOIN menus m ON p.id_menu = m.id_menu 
            WHERE p.id_student = %s AND p.fecha_consumo = CURDATE()
            LIMIT 1
        """, (hijo['id_student'],))
        hijo['pedido_hoy'] = cursor.fetchone()

    cursor.close()
    conn.close()
    return render_template('dashboard.html', hijos=hijos, nombre_padre=session['user_name'], saldo=saldo_padre)

# --- RECARGA ---
@app.route('/recargar', methods=['POST'])
def recargar():
    if 'user_id' not in session: 
        return redirect(url_for('index'))
    
    # Recogemos los nuevos datos del formulario
    monto = request.form.get('monto')
    metodo_pago = request.form.get('metodo_pago') # Transferencia, Débito, Pensión
    tipo_plan = request.form.get('tipo_plan')     # 'semanal' o 'mensual'
    id_padre = session['user_id']
    
    # Calculamos la fecha de vencimiento según el plan seleccionado
    fecha_hoy = datetime.now()
    
    if tipo_plan == 'semanal':
        # Calculamos cuántos días faltan para el próximo viernes (weekday 4)
        # Si hoy es sábado (5), sumamos para llegar al viernes de la siguiente semana
        dias_hasta_viernes = (4 - fecha_hoy.weekday() + 7) % 7
        if dias_hasta_viernes == 0: dias_hasta_viernes = 7 # Si ya es viernes, mover al siguiente
        
        vencimiento = fecha_hoy + timedelta(days=dias_hasta_viernes)
    else:
        # Para el mensual, mantenemos los 30 días o podrías ajustarlo al fin de mes
        vencimiento = fecha_hoy + timedelta(days=30)
    
    nueva_fecha = vencimiento.strftime('%Y-%m-%d')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Actualizamos saldo y fecha_vencimiento (asegúrate que la columna exista en la tabla padres)
    query = "UPDATE padres SET saldo = saldo + %s, fecha_vencimiento = %s WHERE id_app = %s"
    cursor.execute(query, (monto, nueva_fecha, id_padre))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    # Mensaje de éxito con los detalles del pago
    return f"""
    <script>
        alert('Recarga de ${monto} exitosa vía {metodo_pago}. Su plan ({tipo_plan}) vence el: {nueva_fecha}');
        window.location.href = '/dashboard';
    </script>
    """

# --- SELECCIÓN DE MENÚ ---
@app.route('/seleccionar_menu/<int:id_estudiante>')
def seleccionar_menu(id_estudiante):
    if 'user_id' not in session: return redirect(url_for('index'))
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    cursor.execute("SELECT saldo FROM padres WHERE id_app = %s", (session['user_id'],))
    padre_data = cursor.fetchone()
    saldo_actual = padre_data['saldo'] if padre_data else 0.0
    
    cursor.execute("SELECT p.firstname, p.lastname FROM students s JOIN people p ON s.person_id = p.id WHERE s.person_id = %s", (id_estudiante,))
    estudiante = cursor.fetchone()
    
    # --- Separamos platos de restricciones ---
    
    # Solo los que tienen precio (para vender)
    cursor.execute("SELECT * FROM menus WHERE precio > 0")
    platos = cursor.fetchall()
    
    # Solo los que tienen precio 0 (para restringir)
    cursor.execute("SELECT * FROM menus WHERE precio = 0")
    restricciones_opciones = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    # Enviamos ambas listas al template
    return render_template('menu.html', 
                           platos=platos, 
                           restricciones_opciones=restricciones_opciones, 
                           estudiante=estudiante, 
                           id_estudiante=id_estudiante, 
                           saldo=saldo_actual)

# --- GUARDAR MENÚ SEMANAL ---
@app.route('/guardar_menu/<int:id_estudiante>', methods=['POST'])
def guardar_menu(id_estudiante):
    if 'user_id' not in session: return redirect(url_for('index'))
    
    # 1. Obtener y validar que la fecha no venga vacía
    fecha_inicio_raw = request.form.get('fecha_inicio')
    if not fecha_inicio_raw:
        return "Error: Debe seleccionar una fecha de inicio. <a href='javascript:history.back()'>Volver</a>"

    fecha_lunes_dt = datetime.strptime(fecha_inicio_raw, '%Y-%m-%d')
    fecha_lunes = fecha_lunes_dt.date()
    hoy = datetime.now().date()
    
    restricciones = request.form.getlist('restriccion')
    dias_semana = {'menu_Lunes': 0, 'menu_Martes': 1, 'menu_Miércoles': 2, 'menu_Jueves': 3, 'menu_Viernes': 4}

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    try:
        # 2. Consultar saldo y vencimiento del padre antes de nada
        cursor.execute("SELECT saldo, fecha_vencimiento FROM padres WHERE id_app = %s", (session['user_id'],))
        padre_data = cursor.fetchone()

        # --- VALIDACIONES DE FECHA ---
        # A. No permitir fechas pasadas
        if fecha_lunes < hoy:
            return "Error: No puede planificar para fechas que ya pasaron. <a href='javascript:history.back()'>Volver</a>"

        # B. Verificar que la fecha esté dentro del plan (cobertura)
        if padre_data['fecha_vencimiento'] and fecha_lunes > padre_data['fecha_vencimiento']:
            return f"""
            <script>
                alert('Error: Fecha fuera del parámetro de su plan. Su cobertura vence el: {padre_data['fecha_vencimiento']}');
                window.location.href = '/seleccionar_menu/{id_estudiante}';
            </script>
            """

        total_a_pagar = 0
        pedidos_a_insertar = []

        # Procesar los platos seleccionados
        for campo, incremento in dias_semana.items():
            id_m = request.form.get(campo)
            if id_m and id_m != "0":
                cursor.execute("SELECT precio FROM menus WHERE id_menu = %s", (id_m,))
                menu_res = cursor.fetchone()
                if menu_res:
                    total_a_pagar += float(menu_res['precio'])
                    fecha_real = fecha_lunes + timedelta(days=incremento)
                    pedidos_a_insertar.append((id_estudiante, id_m, fecha_real))

        # 3. Validar saldo y ejecutar transacciones
        if float(padre_data['saldo']) >= total_a_pagar:
            # Descontar saldo
            cursor.execute("UPDATE padres SET saldo = saldo - %s WHERE id_app = %s", (total_a_pagar, session['user_id']))
            
            # Insertar pedidos
            for p in pedidos_a_insertar:
                cursor.execute("INSERT INTO pedidos (id_student, id_menu, fecha_consumo, estado) VALUES (%s, %s, %s, 'Pendiente')", p)
            
            # Actualizar restricciones
            cursor.execute("DELETE FROM restricciones_alimentos WHERE id_student = %s", (id_estudiante,))
            for r_id in restricciones:
                cursor.execute("INSERT INTO restricciones_alimentos (id_student, id_menu) VALUES (%s, %s)", (id_estudiante, r_id))
            
            conn.commit()
            
            # Éxito: Alerta y regreso al Dashboard
            return """
            <script>
                alert('Planificación guardada y pagada exitosamente.');
                window.location.href = '/dashboard';
            </script>
            """
        else:
            return "Saldo insuficiente. <a href='/dashboard'>Volver al Dashboard para recargar</a>"

    except Exception as e:
        return f"Error en el sistema: {e}"
    finally:
        cursor.close()
        conn.close()

# --- PANEL DEL BAR ---
@app.route('/panel_bar')
def panel_bar():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True, buffered=True)
    
    query_pedidos = """
        SELECT p.id_pedido, p.id_student, pe.firstname, pe.lastname, m.nombre_item, p.estado
        FROM pedidos p
        JOIN students s ON p.id_student = s.person_id
        JOIN people pe ON s.person_id = pe.id
        JOIN menus m ON p.id_menu = m.id_menu
        WHERE p.fecha_consumo = CURDATE()
    """
    cursor.execute(query_pedidos)
    entregas = cursor.fetchall()

    for entrega in entregas:
        cursor.execute("""
            SELECT m.nombre_item FROM restricciones_alimentos r 
            JOIN menus m ON r.id_menu = m.id_menu WHERE r.id_student = %s
        """, (entrega['id_student'],))
        entrega['prohibidos'] = [r['nombre_item'] for r in cursor.fetchall()]

    cursor.close()
    conn.close()
    return render_template('bar.html', entregas=entregas, hoy=datetime.now().date())

@app.route('/entregar_pedido/<int:id_pedido>', methods=['POST'])
def entregar_pedido(id_pedido):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE pedidos SET estado = 'Entregado' WHERE id_pedido = %s", (id_pedido,))
    conn.commit()
    cursor.close()
    conn.close()
    return redirect(url_for('panel_bar'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)