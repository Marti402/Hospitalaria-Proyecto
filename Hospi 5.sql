DROP SCHEMA IF EXISTS hospi;
CREATE SCHEMA hospi;
USE hospi;


CREATE TABLE registro_entrada (
    id_registro INT AUTO_INCREMENT PRIMARY KEY,
    id_med BIGINT NULL,
    id_config INT NULL,

    temperatura_ambiente FLOAT NULL,
    humedad_ambiente FLOAT NULL,

    fecha_caducidad DATE,
    cantidad INT,

    temp_min_med FLOAT,
    temp_max_med FLOAT,
    hum_min_med FLOAT,
    hum_max_med FLOAT,

    tiempo_gracia_minutos INT DEFAULT 60,

    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================
-- ALERTAS
-- =========================================================

CREATE TABLE alertas (
    id_alerta INT AUTO_INCREMENT PRIMARY KEY,
    id_med BIGINT NULL,
    id_config INT NULL,
    tipo_alerta VARCHAR(80),
    mensaje VARCHAR(255),
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =========================================================
-- CONFIGURACIÓN DE ESPACIOS
-- =========================================================

CREATE TABLE configuracion_espacio (
    id_config INT PRIMARY KEY AUTO_INCREMENT,
    nombre_espacio VARCHAR(100),

    temp_min_espacio FLOAT,
    temp_max_espacio FLOAT,
    hum_min_espacio FLOAT,
    hum_max_espacio FLOAT,

    temp_min_control FLOAT NULL,
    temp_max_control FLOAT NULL,
    hum_min_control FLOAT NULL,
    hum_max_control FLOAT NULL,

    seleccionada BOOLEAN DEFAULT FALSE
);

-- =========================================================
-- MEDICAMENTOS
-- =========================================================

CREATE TABLE medicamentos (
    id_med BIGINT PRIMARY KEY,
    id_config INT NOT NULL,

    temp_min FLOAT,
    temp_max FLOAT,
    hum_min FLOAT,
    hum_max FLOAT,

    tiempo_gracia_minutos INT DEFAULT 60,

    CONSTRAINT fk_medicamentos_configuracion
        FOREIGN KEY (id_config)
        REFERENCES configuracion_espacio(id_config)
        ON UPDATE CASCADE
);

-- =========================================================
-- CADUCIDADES
-- Si se elimina un medicamento activo, su caducidad activa también desaparece.
-- El histórico se conserva en historico_logs.
-- =========================================================

CREATE TABLE caducidades (
    id_med BIGINT PRIMARY KEY,
    fecha_caducidad DATE,
    caducado BOOLEAN DEFAULT FALSE,
    dias_restantes INT,
    aviso_caducidad BOOLEAN DEFAULT FALSE,
    motivo_caducidad VARCHAR(255) NULL,

    CONSTRAINT fk_caducidades_medicamentos
        FOREIGN KEY (id_med)
        REFERENCES medicamentos(id_med)
        ON DELETE CASCADE
        ON UPDATE CASCADE
);

-- =========================================================
-- STOCK
-- Si se elimina un medicamento activo, su stock activo también desaparece.
-- Los movimientos de stock se conservan aparte.
-- =========================================================

CREATE TABLE stock (
    id_med BIGINT PRIMARY KEY,
    cantidad INT NOT NULL DEFAULT 0,
    stock_min INT NOT NULL DEFAULT 5,
    ultima_actualizacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_stock_medicamentos
        FOREIGN KEY (id_med)
        REFERENCES medicamentos(id_med)
        ON DELETE CASCADE
        ON UPDATE CASCADE
);

CREATE TABLE movimientos_stock (
    id_mov INT AUTO_INCREMENT PRIMARY KEY,
    id_med BIGINT,
    tipo_mov VARCHAR(20),
    cantidad INT,
    fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    observaciones VARCHAR(255)
);

-- =========================================================
-- HISTÓRICO GENERAL
-- No lleva FK contra medicamentos porque debe conservar el histórico
-- incluso cuando el medicamento desaparece de la tabla activa.
-- =========================================================

CREATE TABLE historico_logs (
    id_log INT AUTO_INCREMENT PRIMARY KEY,
    id_med BIGINT,
    id_config INT,

    temp_min FLOAT,
    temp_max FLOAT,
    hum_min FLOAT,
    hum_max FLOAT,

    fecha_caducidad DATE,
    fecha_primera_insercion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    fecha_ultima_salida TIMESTAMP NULL,
    cantidad_inicial INT,

    estado_actual VARCHAR(50) DEFAULT 'ACTIVO',
    observaciones VARCHAR(255)
);

-- =========================================================
-- HISTÓRICO DE MÁXIMOS Y MÍNIMOS DEL ESPACIO
-- =========================================================

CREATE TABLE historial_espacio (
    id_config INT PRIMARY KEY,
    temp_max_hist FLOAT,
    temp_min_hist FLOAT,
    hum_max_hist FLOAT,
    hum_min_hist FLOAT,

    CONSTRAINT fk_historial_espacio_configuracion
        FOREIGN KEY (id_config)
        REFERENCES configuracion_espacio(id_config)
        ON UPDATE CASCADE
);

-- =========================================================
-- MEDICIONES REALES DEL ESPACIO
-- =========================================================

CREATE TABLE mediciones_espacio (
    id_medicion INT AUTO_INCREMENT PRIMARY KEY,
    id_config INT NOT NULL,
    temperatura FLOAT,
    humedad FLOAT,
    fecha_medicion TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_mediciones_espacio_configuracion
        FOREIGN KEY (id_config)
        REFERENCES configuracion_espacio(id_config)
        ON UPDATE CASCADE
);

-- =========================================================
-- INCIDENCIAS DEL ESPACIO
-- =========================================================

CREATE TABLE incidencias_espacio (
    id_incidencia INT AUTO_INCREMENT PRIMARY KEY,
    id_config INT NOT NULL,

    fecha_inicio TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_fin TIMESTAMP NULL,
    duracion_minutos INT NULL,

    temperatura_inicio FLOAT,
    humedad_inicio FLOAT,
    temperatura_fin FLOAT NULL,
    humedad_fin FLOAT NULL,

    motivo VARCHAR(255),
    estado VARCHAR(20) DEFAULT 'ABIERTA',

    CONSTRAINT fk_incidencias_espacio_configuracion
        FOREIGN KEY (id_config)
        REFERENCES configuracion_espacio(id_config)
        ON UPDATE CASCADE
);

-- =========================================================
-- INCIDENCIAS POR MEDICAMENTO
-- id_med permite NULL para conservar la incidencia histórica aunque
-- el medicamento se elimine de la tabla activa.
-- =========================================================

CREATE TABLE incidencias_medicamento (
    id_incidencia_med INT AUTO_INCREMENT PRIMARY KEY,

    id_med BIGINT NULL,
    id_config INT NOT NULL,

    fecha_inicio TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_fin TIMESTAMP NULL,
    duracion_minutos INT NULL,

    temperatura_inicio FLOAT,
    humedad_inicio FLOAT,
    temperatura_fin FLOAT NULL,
    humedad_fin FLOAT NULL,

    aviso_emitido BOOLEAN DEFAULT FALSE,
    caducado_por_incidencia BOOLEAN DEFAULT FALSE,

    motivo VARCHAR(255),
    estado VARCHAR(20) DEFAULT 'ABIERTA',

    CONSTRAINT fk_incidencias_medicamento_medicamentos
        FOREIGN KEY (id_med)
        REFERENCES medicamentos(id_med)
        ON DELETE SET NULL
        ON UPDATE CASCADE,

    CONSTRAINT fk_incidencias_medicamento_configuracion
        FOREIGN KEY (id_config)
        REFERENCES configuracion_espacio(id_config)
        ON UPDATE CASCADE
);

-- =========================================================
-- PROCEDIMIENTOS
-- =========================================================

DELIMITER //

-- =========================================================
-- SELECCIONAR ESPACIO ACTIVO
-- =========================================================

CREATE PROCEDURE seleccionar_configuracion(IN p_id_config INT)
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM configuracion_espacio
        WHERE id_config = p_id_config
    ) THEN

        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'El espacio indicado no existe';

    ELSE

        UPDATE configuracion_espacio
        SET seleccionada = FALSE;

        UPDATE configuracion_espacio
        SET seleccionada = TRUE
        WHERE id_config = p_id_config;

    END IF;
END //

-- =========================================================
-- RECALCULAR RANGO COMÚN DE CONTROL
-- Dinámico:
-- - Usa SIEMPRE los medicamentos activos actuales.
-- - Si se elimina el medicamento más restrictivo, el rango se relaja.
-- - Si no quedan medicamentos en el espacio, vuelve al rango físico del espacio.
-- =========================================================

CREATE PROCEDURE recalcular_rango_control(IN p_id_config INT)
BEGIN
    UPDATE configuracion_espacio ce
    LEFT JOIN (
        SELECT
            id_config,
            MAX(temp_min) AS max_temp_min,
            MIN(temp_max) AS min_temp_max,
            MAX(hum_min) AS max_hum_min,
            MIN(hum_max) AS min_hum_max
        FROM medicamentos
        WHERE id_config = p_id_config
        GROUP BY id_config
    ) meds ON meds.id_config = ce.id_config
    SET
        ce.temp_min_control = GREATEST(
            ce.temp_min_espacio,
            COALESCE(meds.max_temp_min, ce.temp_min_espacio)
        ),

        ce.temp_max_control = LEAST(
            ce.temp_max_espacio,
            COALESCE(meds.min_temp_max, ce.temp_max_espacio)
        ),

        ce.hum_min_control = GREATEST(
            ce.hum_min_espacio,
            COALESCE(meds.max_hum_min, ce.hum_min_espacio)
        ),

        ce.hum_max_control = LEAST(
            ce.hum_max_espacio,
            COALESCE(meds.min_hum_max, ce.hum_max_espacio)
        )
    WHERE ce.id_config = p_id_config;
END //

-- =========================================================
-- RECALCULAR TODOS LOS ESPACIOS
-- Útil después de cargas masivas o correcciones manuales.
-- =========================================================

CREATE PROCEDURE recalcular_todos_los_rangos_control()
BEGIN
    DECLARE done BOOLEAN DEFAULT FALSE;
    DECLARE v_id_config INT;

    DECLARE cur_configs CURSOR FOR
        SELECT id_config
        FROM configuracion_espacio;

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;

    OPEN cur_configs;

    configs_loop: LOOP
        FETCH cur_configs INTO v_id_config;

        IF done THEN
            LEAVE configs_loop;
        END IF;

        CALL recalcular_rango_control(v_id_config);
    END LOOP;

    CLOSE cur_configs;
END //

-- =========================================================
-- COMPROBAR COMPATIBILIDAD DEL MEDICAMENTO
-- =========================================================

CREATE PROCEDURE comprobar_compatibilidad_medicamento(
    IN p_id_config INT,
    IN p_id_med BIGINT,
    IN p_temp_min FLOAT,
    IN p_temp_max FLOAT,
    IN p_hum_min FLOAT,
    IN p_hum_max FLOAT,
    OUT p_valido BOOLEAN,
    OUT p_mensaje VARCHAR(255)
)
BEGIN
    DECLARE v_temp_min_esp FLOAT;
    DECLARE v_temp_max_esp FLOAT;
    DECLARE v_hum_min_esp FLOAT;
    DECLARE v_hum_max_esp FLOAT;

    DECLARE v_temp_min_final FLOAT;
    DECLARE v_temp_max_final FLOAT;
    DECLARE v_hum_min_final FLOAT;
    DECLARE v_hum_max_final FLOAT;

    DECLARE v_max_temp_min_existente FLOAT;
    DECLARE v_min_temp_max_existente FLOAT;
    DECLARE v_max_hum_min_existente FLOAT;
    DECLARE v_min_hum_max_existente FLOAT;

    SET p_valido = TRUE;
    SET p_mensaje = 'Medicamento compatible con el espacio';

    IF NOT EXISTS (
        SELECT 1
        FROM configuracion_espacio
        WHERE id_config = p_id_config
    ) THEN
        SET p_valido = FALSE;
        SET p_mensaje = 'El espacio indicado no existe';
    END IF;

    IF p_valido = TRUE THEN

        SELECT temp_min_espacio, temp_max_espacio, hum_min_espacio, hum_max_espacio
        INTO v_temp_min_esp, v_temp_max_esp, v_hum_min_esp, v_hum_max_esp
        FROM configuracion_espacio
        WHERE id_config = p_id_config;

        IF p_temp_min IS NULL OR p_temp_max IS NULL OR p_hum_min IS NULL OR p_hum_max IS NULL THEN
            SET p_valido = FALSE;
            SET p_mensaje = 'Faltan rangos de conservacion del medicamento';

        ELSEIF p_temp_min >= p_temp_max THEN
            SET p_valido = FALSE;
            SET p_mensaje = 'Rango de temperatura del medicamento incorrecto';

        ELSEIF p_hum_min >= p_hum_max THEN
            SET p_valido = FALSE;
            SET p_mensaje = 'Rango de humedad del medicamento incorrecto';

        ELSEIF p_temp_max < v_temp_min_esp OR p_temp_min > v_temp_max_esp THEN
            SET p_valido = FALSE;
            SET p_mensaje = 'Medicamento no permitido: su temperatura no coincide con el rango fisico del espacio';

        ELSEIF p_hum_max < v_hum_min_esp OR p_hum_min > v_hum_max_esp THEN
            SET p_valido = FALSE;
            SET p_mensaje = 'Medicamento no permitido: su humedad no coincide con el rango fisico del espacio';
        END IF;

    END IF;

    IF p_valido = TRUE THEN

        SELECT
            MAX(temp_min),
            MIN(temp_max),
            MAX(hum_min),
            MIN(hum_max)
        INTO
            v_max_temp_min_existente,
            v_min_temp_max_existente,
            v_max_hum_min_existente,
            v_min_hum_max_existente
        FROM medicamentos
        WHERE id_config = p_id_config
          AND id_med <> p_id_med;

        SET v_temp_min_final = GREATEST(
            v_temp_min_esp,
            COALESCE(v_max_temp_min_existente, p_temp_min),
            p_temp_min
        );

        SET v_temp_max_final = LEAST(
            v_temp_max_esp,
            COALESCE(v_min_temp_max_existente, p_temp_max),
            p_temp_max
        );

        SET v_hum_min_final = GREATEST(
            v_hum_min_esp,
            COALESCE(v_max_hum_min_existente, p_hum_min),
            p_hum_min
        );

        SET v_hum_max_final = LEAST(
            v_hum_max_esp,
            COALESCE(v_min_hum_max_existente, p_hum_max),
            p_hum_max
        );

        IF v_temp_min_final >= v_temp_max_final THEN
            SET p_valido = FALSE;
            SET p_mensaje = 'Medicamento no permitido: no existe rango comun de temperatura';

        ELSEIF v_hum_min_final >= v_hum_max_final THEN
            SET p_valido = FALSE;
            SET p_mensaje = 'Medicamento no permitido: no existe rango comun de humedad';

        ELSEIF v_temp_max_final - v_temp_min_final < 5 THEN
            SET p_valido = FALSE;
            SET p_mensaje = 'Medicamento no permitido: el rango comun de temperatura queda por debajo de 5 grados';

        ELSEIF v_hum_max_final - v_hum_min_final < 10 THEN
            SET p_valido = FALSE;
            SET p_mensaje = 'Medicamento no permitido: el rango comun de humedad queda por debajo del 10 por ciento';
        END IF;

    END IF;
END //

-- =========================================================
-- COMPROBAR CADUCIDAD POR FECHA
-- =========================================================

CREATE PROCEDURE comprobar_caducidad()
BEGIN
    UPDATE caducidades
    SET 
        dias_restantes = DATEDIFF(fecha_caducidad, CURDATE()),
        caducado = caducado OR fecha_caducidad < CURDATE(),
        aviso_caducidad = fecha_caducidad BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 5 DAY);

    INSERT INTO alertas (id_med, tipo_alerta, mensaje)
    SELECT id_med, 'AVISO CADUCIDAD', 'El medicamento caduca en 5 dias o menos'
    FROM caducidades c
    WHERE aviso_caducidad = TRUE
      AND caducado = FALSE
      AND NOT EXISTS (
          SELECT 1
          FROM alertas a
          WHERE a.id_med = c.id_med
            AND a.tipo_alerta = 'AVISO CADUCIDAD'
      );

    INSERT INTO alertas (id_med, tipo_alerta, mensaje)
    SELECT id_med, 'CADUCADO', 'El medicamento ya esta caducado'
    FROM caducidades c
    WHERE caducado = TRUE
      AND NOT EXISTS (
          SELECT 1
          FROM alertas a
          WHERE a.id_med = c.id_med
            AND a.tipo_alerta = 'CADUCADO'
      );
END //

-- =========================================================
-- ACTUALIZAR CADUCIDAD
-- =========================================================

CREATE PROCEDURE actualizar_caducidad(
    IN p_id_med BIGINT,
    IN p_fecha_caducidad DATE
)
BEGIN
    IF p_fecha_caducidad IS NULL THEN

        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'La fecha de caducidad no puede ser NULL';

    ELSEIF EXISTS (SELECT 1 FROM caducidades WHERE id_med = p_id_med) THEN

        UPDATE caducidades
        SET fecha_caducidad = p_fecha_caducidad,
            dias_restantes = DATEDIFF(p_fecha_caducidad, CURDATE()),
            caducado = p_fecha_caducidad < CURDATE(),
            aviso_caducidad = p_fecha_caducidad BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 5 DAY),
            motivo_caducidad = NULL
        WHERE id_med = p_id_med;

    ELSEIF EXISTS (SELECT 1 FROM historico_logs WHERE id_med = p_id_med) THEN

        UPDATE historico_logs
        SET fecha_caducidad = p_fecha_caducidad,
            observaciones = 'Fecha de caducidad actualizada en historico'
        WHERE id_med = p_id_med;

    ELSE

        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'El medicamento no existe ni en activo ni en historico';

    END IF;
END //

-- =========================================================
-- REGISTRAR MEDICIÓN DEL ESPACIO
-- =========================================================

CREATE PROCEDURE registrar_medicion_espacio(
    IN p_id_config INT,
    IN p_temp FLOAT,
    IN p_humedad FLOAT
)
BEGIN
    DECLARE v_temp_min_control FLOAT;
    DECLARE v_temp_max_control FLOAT;
    DECLARE v_hum_min_control FLOAT;
    DECLARE v_hum_max_control FLOAT;

    DECLARE v_fuera_rango_espacio BOOLEAN DEFAULT FALSE;
    DECLARE v_incidencia_espacio_abierta INT DEFAULT 0;
    DECLARE v_id_incidencia_espacio INT DEFAULT NULL;

    DECLARE done BOOLEAN DEFAULT FALSE;

    DECLARE v_id_med BIGINT;
    DECLARE v_temp_min_med FLOAT;
    DECLARE v_temp_max_med FLOAT;
    DECLARE v_hum_min_med FLOAT;
    DECLARE v_hum_max_med FLOAT;
    DECLARE v_tiempo_gracia INT;

    DECLARE v_fuera_rango_med BOOLEAN;
    DECLARE v_incidencia_med_abierta INT DEFAULT 0;
    DECLARE v_id_incidencia_med INT DEFAULT NULL;
    DECLARE v_fecha_inicio TIMESTAMP;
    DECLARE v_duracion INT;
    DECLARE v_aviso_emitido BOOLEAN;
    DECLARE v_ya_caducado BOOLEAN;

    DECLARE cur_meds CURSOR FOR
        SELECT id_med, temp_min, temp_max, hum_min, hum_max, tiempo_gracia_minutos
        FROM medicamentos
        WHERE id_config = p_id_config;

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;

    IF p_temp IS NULL OR p_humedad IS NULL THEN

        INSERT INTO alertas (id_config, tipo_alerta, mensaje)
        VALUES (
            p_id_config,
            'ERROR MEDICION',
            'No se puede registrar una medicion con temperatura o humedad NULL'
        );

    ELSEIF NOT EXISTS (
        SELECT 1
        FROM configuracion_espacio
        WHERE id_config = p_id_config
    ) THEN

        INSERT INTO alertas (id_config, tipo_alerta, mensaje)
        VALUES (
            p_id_config,
            'ERROR ESPACIO',
            'No existe el espacio indicado para registrar la medicion'
        );

    ELSE

        CALL recalcular_rango_control(p_id_config);

        INSERT INTO mediciones_espacio (id_config, temperatura, humedad)
        VALUES (p_id_config, p_temp, p_humedad);

        INSERT INTO historial_espacio (
            id_config,
            temp_max_hist,
            temp_min_hist,
            hum_max_hist,
            hum_min_hist
        )
        VALUES (
            p_id_config,
            p_temp,
            p_temp,
            p_humedad,
            p_humedad
        )
        ON DUPLICATE KEY UPDATE
            temp_max_hist = GREATEST(temp_max_hist, p_temp),
            temp_min_hist = LEAST(temp_min_hist, p_temp),
            hum_max_hist = GREATEST(hum_max_hist, p_humedad),
            hum_min_hist = LEAST(hum_min_hist, p_humedad);

        SELECT temp_min_control, temp_max_control, hum_min_control, hum_max_control
        INTO v_temp_min_control, v_temp_max_control, v_hum_min_control, v_hum_max_control
        FROM configuracion_espacio
        WHERE id_config = p_id_config;

        IF p_temp < v_temp_min_control OR p_temp > v_temp_max_control
           OR p_humedad < v_hum_min_control OR p_humedad > v_hum_max_control THEN

            SET v_fuera_rango_espacio = TRUE;

        END IF;

        SELECT COUNT(*)
        INTO v_incidencia_espacio_abierta
        FROM incidencias_espacio
        WHERE id_config = p_id_config
          AND estado = 'ABIERTA';

        IF v_fuera_rango_espacio = TRUE AND v_incidencia_espacio_abierta = 0 THEN

            INSERT INTO incidencias_espacio (
                id_config,
                temperatura_inicio,
                humedad_inicio,
                motivo,
                estado
            )
            VALUES (
                p_id_config,
                p_temp,
                p_humedad,
                'El espacio esta fuera del rango comun de control',
                'ABIERTA'
            );

            INSERT INTO alertas (id_config, tipo_alerta, mensaje)
            VALUES (
                p_id_config,
                'ESPACIO FUERA DE RANGO',
                'El espacio ha salido del rango comun calculado para los medicamentos almacenados'
            );

        END IF;

        IF v_fuera_rango_espacio = FALSE AND v_incidencia_espacio_abierta > 0 THEN

            SELECT id_incidencia
            INTO v_id_incidencia_espacio
            FROM incidencias_espacio
            WHERE id_config = p_id_config
              AND estado = 'ABIERTA'
            LIMIT 1;

            UPDATE incidencias_espacio
            SET fecha_fin = CURRENT_TIMESTAMP,
                duracion_minutos = TIMESTAMPDIFF(MINUTE, fecha_inicio, CURRENT_TIMESTAMP),
                temperatura_fin = p_temp,
                humedad_fin = p_humedad,
                estado = 'CERRADA'
            WHERE id_incidencia = v_id_incidencia_espacio;

        END IF;

        OPEN cur_meds;

        meds_loop: LOOP

            FETCH cur_meds INTO
                v_id_med,
                v_temp_min_med,
                v_temp_max_med,
                v_hum_min_med,
                v_hum_max_med,
                v_tiempo_gracia;

            IF done THEN
                LEAVE meds_loop;
            END IF;

            SET v_fuera_rango_med = FALSE;

            IF p_temp < v_temp_min_med OR p_temp > v_temp_max_med
               OR p_humedad < v_hum_min_med OR p_humedad > v_hum_max_med THEN

                SET v_fuera_rango_med = TRUE;

            END IF;

            SELECT COUNT(*)
            INTO v_incidencia_med_abierta
            FROM incidencias_medicamento
            WHERE id_med = v_id_med
              AND estado = 'ABIERTA';

            IF v_fuera_rango_med = TRUE AND v_incidencia_med_abierta = 0 THEN

                INSERT INTO incidencias_medicamento (
                    id_med,
                    id_config,
                    temperatura_inicio,
                    humedad_inicio,
                    motivo,
                    estado
                )
                VALUES (
                    v_id_med,
                    p_id_config,
                    p_temp,
                    p_humedad,
                    'Medicamento fuera de su rango propio de conservacion',
                    'ABIERTA'
                );

            ELSEIF v_fuera_rango_med = TRUE AND v_incidencia_med_abierta > 0 THEN

                SELECT id_incidencia_med, fecha_inicio, aviso_emitido, caducado_por_incidencia
                INTO v_id_incidencia_med, v_fecha_inicio, v_aviso_emitido, v_ya_caducado
                FROM incidencias_medicamento
                WHERE id_med = v_id_med
                  AND estado = 'ABIERTA'
                LIMIT 1;

                SET v_duracion = TIMESTAMPDIFF(MINUTE, v_fecha_inicio, CURRENT_TIMESTAMP);

                IF v_duracion >= v_tiempo_gracia / 2
                   AND v_aviso_emitido = FALSE
                   AND v_ya_caducado = FALSE THEN

                    INSERT INTO alertas (id_med, id_config, tipo_alerta, mensaje)
                    VALUES (
                        v_id_med,
                        p_id_config,
                        'AVISO FUERA DE RANGO',
                        'El medicamento ha superado la mitad del tiempo de gracia fuera de rango'
                    );

                    UPDATE incidencias_medicamento
                    SET aviso_emitido = TRUE
                    WHERE id_incidencia_med = v_id_incidencia_med;

                END IF;

                IF v_duracion >= v_tiempo_gracia
                   AND v_ya_caducado = FALSE THEN

                    UPDATE caducidades
                    SET caducado = TRUE,
                        motivo_caducidad = 'Caducado automaticamente por superar el tiempo de gracia fuera de rango'
                    WHERE id_med = v_id_med;

                    UPDATE incidencias_medicamento
                    SET caducado_por_incidencia = TRUE
                    WHERE id_incidencia_med = v_id_incidencia_med;

                    UPDATE historico_logs
                    SET estado_actual = 'CADUCADO',
                        observaciones = 'Caducado automaticamente por incidencia ambiental'
                    WHERE id_med = v_id_med;

                    INSERT INTO alertas (id_med, id_config, tipo_alerta, mensaje)
                    VALUES (
                        v_id_med,
                        p_id_config,
                        'CADUCADO POR AMBIENTE',
                        'El medicamento ha caducado por superar el tiempo de gracia fuera de rango'
                    );

                END IF;

            ELSEIF v_fuera_rango_med = FALSE AND v_incidencia_med_abierta > 0 THEN

                SELECT id_incidencia_med
                INTO v_id_incidencia_med
                FROM incidencias_medicamento
                WHERE id_med = v_id_med
                  AND estado = 'ABIERTA'
                LIMIT 1;

                UPDATE incidencias_medicamento
                SET fecha_fin = CURRENT_TIMESTAMP,
                    duracion_minutos = TIMESTAMPDIFF(MINUTE, fecha_inicio, CURRENT_TIMESTAMP),
                    temperatura_fin = p_temp,
                    humedad_fin = p_humedad,
                    estado = 'CERRADA'
                WHERE id_incidencia_med = v_id_incidencia_med;

            END IF;

        END LOOP;

        CLOSE cur_meds;

    END IF;
END //

-- =========================================================
-- REPONER STOCK
-- =========================================================

CREATE PROCEDURE reponer_stock(
    IN p_id_med BIGINT,
    IN p_cantidad INT,
    IN p_fecha_caducidad DATE
)
BEGIN
    IF p_cantidad <= 0 THEN

        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'La cantidad a reponer debe ser mayor que 0';

    END IF;

    IF EXISTS (SELECT 1 FROM stock WHERE id_med = p_id_med) THEN

        UPDATE stock
        SET cantidad = cantidad + p_cantidad
        WHERE id_med = p_id_med;

        CALL actualizar_caducidad(p_id_med, p_fecha_caducidad);

        INSERT INTO movimientos_stock (id_med, tipo_mov, cantidad, observaciones)
        VALUES (p_id_med, 'ENTRADA', p_cantidad, 'Reposicion de stock');

        DELETE FROM alertas
        WHERE id_med = p_id_med
          AND tipo_alerta IN ('SIN STOCK', 'STOCK BAJO');

    ELSE

        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'El medicamento no existe en stock';

    END IF;
END //

-- =========================================================
-- RETIRAR STOCK
-- =========================================================

CREATE PROCEDURE retirar_stock(
    IN p_id_med BIGINT,
    IN p_cantidad INT
)
BEGIN
    DECLARE v_stock_actual INT;
    DECLARE v_stock_min INT;
    DECLARE v_existe INT DEFAULT 0;
    DECLARE v_id_config INT;

    IF p_cantidad <= 0 THEN

        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'La cantidad a retirar debe ser mayor que 0';

    END IF;

    SELECT COUNT(*)
    INTO v_existe
    FROM stock
    WHERE id_med = p_id_med;

    IF v_existe = 0 THEN

        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'El medicamento no existe en stock';

    END IF;

    SELECT cantidad, stock_min
    INTO v_stock_actual, v_stock_min
    FROM stock
    WHERE id_med = p_id_med;

    IF p_cantidad > v_stock_actual THEN

        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'No se puede retirar mas stock del disponible';

    END IF;

    SELECT id_config
    INTO v_id_config
    FROM medicamentos
    WHERE id_med = p_id_med;

    UPDATE stock
    SET cantidad = cantidad - p_cantidad
    WHERE id_med = p_id_med;

    INSERT INTO movimientos_stock (id_med, tipo_mov, cantidad, observaciones)
    VALUES (p_id_med, 'SALIDA', p_cantidad, 'Retirada de stock');

    SELECT cantidad, stock_min
    INTO v_stock_actual, v_stock_min
    FROM stock
    WHERE id_med = p_id_med;

    IF v_stock_actual = 0 THEN

        INSERT INTO alertas (id_med, id_config, tipo_alerta, mensaje)
        SELECT p_id_med, v_id_config, 'SIN STOCK', 'El medicamento se ha quedado sin stock'
        WHERE NOT EXISTS (
            SELECT 1
            FROM alertas
            WHERE id_med = p_id_med
              AND tipo_alerta = 'SIN STOCK'
        );

        UPDATE historico_logs h
        JOIN medicamentos m ON h.id_med = m.id_med
        JOIN caducidades c ON h.id_med = c.id_med
        SET h.estado_actual = 'SIN STOCK',
            h.fecha_ultima_salida = CURRENT_TIMESTAMP,
            h.id_config = m.id_config,
            h.temp_min = m.temp_min,
            h.temp_max = m.temp_max,
            h.hum_min = m.hum_min,
            h.hum_max = m.hum_max,
            h.fecha_caducidad = c.fecha_caducidad,
            h.observaciones = 'Medicamento archivado por quedarse sin stock'
        WHERE h.id_med = p_id_med;

        UPDATE incidencias_medicamento
        SET fecha_fin = CURRENT_TIMESTAMP,
            duracion_minutos = TIMESTAMPDIFF(MINUTE, fecha_inicio, CURRENT_TIMESTAMP),
            estado = 'CERRADA',
            motivo = CONCAT(
                COALESCE(motivo, ''),
                ' | Incidencia cerrada automaticamente al eliminar el medicamento por falta de stock'
            )
        WHERE id_med = p_id_med
          AND estado = 'ABIERTA';

        DELETE FROM stock WHERE id_med = p_id_med;
        DELETE FROM caducidades WHERE id_med = p_id_med;
        DELETE FROM medicamentos WHERE id_med = p_id_med;

        CALL recalcular_rango_control(v_id_config);

    ELSEIF v_stock_actual <= v_stock_min THEN

        INSERT INTO alertas (id_med, id_config, tipo_alerta, mensaje)
        SELECT p_id_med, v_id_config, 'STOCK BAJO', 'El medicamento esta por debajo del stock minimo'
        WHERE NOT EXISTS (
            SELECT 1
            FROM alertas
            WHERE id_med = p_id_med
              AND tipo_alerta = 'STOCK BAJO'
        );

    END IF;
END //

DELIMITER ;

-- =========================================================
-- TRIGGERS
-- =========================================================

DELIMITER $$

-- =========================================================
-- RECÁLCULO AUTOMÁTICO DEL RANGO AL INSERTAR MEDICAMENTO
-- =========================================================

CREATE TRIGGER trg_medicamentos_after_insert
AFTER INSERT ON medicamentos
FOR EACH ROW
BEGIN
    CALL recalcular_rango_control(NEW.id_config);
END $$

-- =========================================================
-- RECÁLCULO AUTOMÁTICO DEL RANGO AL ELIMINAR MEDICAMENTO
-- Este trigger es el cambio clave: si se borra el medicamento
-- limitante, el rango se recalcula con los medicamentos restantes.
-- =========================================================

CREATE TRIGGER trg_medicamentos_after_delete
AFTER DELETE ON medicamentos
FOR EACH ROW
BEGIN
    CALL recalcular_rango_control(OLD.id_config);
END $$

-- =========================================================
-- RECÁLCULO AUTOMÁTICO DEL RANGO AL MODIFICAR MEDICAMENTO
-- Si cambia de espacio, recalcula el espacio antiguo y el nuevo.
-- =========================================================

CREATE TRIGGER trg_medicamentos_after_update
AFTER UPDATE ON medicamentos
FOR EACH ROW
BEGIN
    IF OLD.id_config <> NEW.id_config THEN
        CALL recalcular_rango_control(OLD.id_config);
        CALL recalcular_rango_control(NEW.id_config);
    ELSE
        CALL recalcular_rango_control(NEW.id_config);
    END IF;
END $$

-- =========================================================
-- PROCESAR ENTRADA DESDE LABVIEW / INTERFAZ
-- Ahora permite:
-- 1) insertar/reponer medicamento;
-- 2) registrar solo medición ambiental si id_med es NULL.
-- =========================================================

CREATE TRIGGER procesar_registro_entrada
AFTER INSERT ON registro_entrada
FOR EACH ROW
procesar: BEGIN

    DECLARE v_dias INT;
    DECLARE v_config INT;
    DECLARE v_config_anterior INT DEFAULT NULL;
    DECLARE v_existe_med INT DEFAULT 0;
    DECLARE v_valido BOOLEAN DEFAULT TRUE;
    DECLARE v_mensaje VARCHAR(255);

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_config = NULL;

    IF NEW.id_config IS NOT NULL THEN

        SET v_config = NEW.id_config;

    ELSE

        SELECT id_config
        INTO v_config
        FROM configuracion_espacio
        WHERE seleccionada = TRUE
        LIMIT 1;

    END IF;

    IF v_config IS NULL THEN

        INSERT INTO alertas (id_med, tipo_alerta, mensaje)
        VALUES (
            NEW.id_med,
            'ERROR CONFIGURACION',
            'No hay ningun espacio seleccionado para registrar la entrada'
        );

        LEAVE procesar;

    END IF;

    -- =====================================================
    -- CASO 1: SOLO MEDICIÓN AMBIENTAL
    -- =====================================================

    IF NEW.id_med IS NULL THEN

        IF NEW.temperatura_ambiente IS NOT NULL
           AND NEW.humedad_ambiente IS NOT NULL THEN

            CALL registrar_medicion_espacio(
                v_config,
                NEW.temperatura_ambiente,
                NEW.humedad_ambiente
            );

        ELSE

            INSERT INTO alertas (id_config, tipo_alerta, mensaje)
            VALUES (
                v_config,
                'ENTRADA INVALIDA',
                'La entrada no contiene medicamento ni medicion ambiental completa'
            );

        END IF;

        LEAVE procesar;

    END IF;

    -- =====================================================
    -- CASO 2: INSERCIÓN O REPOSICIÓN DE MEDICAMENTO
    -- =====================================================

    IF NEW.cantidad IS NULL OR NEW.cantidad <= 0 THEN

        INSERT INTO alertas (id_med, id_config, tipo_alerta, mensaje)
        VALUES (
            NEW.id_med,
            v_config,
            'CANTIDAD INVALIDA',
            'El medicamento se ha intentado insertar con cantidad nula o negativa'
        );

        LEAVE procesar;

    END IF;

    IF NEW.fecha_caducidad IS NULL THEN

        INSERT INTO alertas (id_med, id_config, tipo_alerta, mensaje)
        VALUES (
            NEW.id_med,
            v_config,
            'FECHA CADUCIDAD INVALIDA',
            'El medicamento se ha intentado insertar sin fecha de caducidad'
        );

        LEAVE procesar;

    END IF;

    SET v_dias = DATEDIFF(NEW.fecha_caducidad, CURDATE());

    CALL comprobar_compatibilidad_medicamento(
        v_config,
        NEW.id_med,
        NEW.temp_min_med,
        NEW.temp_max_med,
        NEW.hum_min_med,
        NEW.hum_max_med,
        v_valido,
        v_mensaje
    );

    IF v_valido = FALSE THEN

        INSERT INTO alertas (id_med, id_config, tipo_alerta, mensaje)
        VALUES (
            NEW.id_med,
            v_config,
            'MEDICAMENTO NO PERMITIDO',
            v_mensaje
        );

        LEAVE procesar;

    END IF;

    SELECT COUNT(*)
    INTO v_existe_med
    FROM medicamentos
    WHERE id_med = NEW.id_med;

    IF v_existe_med = 0 THEN

        INSERT INTO medicamentos (
            id_med,
            id_config,
            temp_min,
            temp_max,
            hum_min,
            hum_max,
            tiempo_gracia_minutos
        )
        VALUES (
            NEW.id_med,
            v_config,
            NEW.temp_min_med,
            NEW.temp_max_med,
            NEW.hum_min_med,
            NEW.hum_max_med,
            COALESCE(NEW.tiempo_gracia_minutos, 60)
        );

        INSERT INTO caducidades (
            id_med,
            fecha_caducidad,
            caducado,
            dias_restantes,
            aviso_caducidad
        )
        VALUES (
            NEW.id_med,
            NEW.fecha_caducidad,
            NEW.fecha_caducidad < CURDATE(),
            v_dias,
            NEW.fecha_caducidad BETWEEN CURDATE() AND DATE_ADD(CURDATE(), INTERVAL 5 DAY)
        );

        INSERT INTO stock (id_med, cantidad, stock_min)
        VALUES (NEW.id_med, NEW.cantidad, 5);

        INSERT INTO movimientos_stock (id_med, tipo_mov, cantidad, observaciones)
        VALUES (
            NEW.id_med,
            'ENTRADA',
            NEW.cantidad,
            'Insercion inicial del medicamento'
        );

        INSERT INTO historico_logs (
            id_med,
            id_config,
            temp_min,
            temp_max,
            hum_min,
            hum_max,
            fecha_caducidad,
            cantidad_inicial,
            estado_actual,
            observaciones
        )
        VALUES (
            NEW.id_med,
            v_config,
            NEW.temp_min_med,
            NEW.temp_max_med,
            NEW.hum_min_med,
            NEW.hum_max_med,
            NEW.fecha_caducidad,
            NEW.cantidad,
            'ACTIVO',
            'Primera insercion en el sistema'
        );

    ELSE

        SELECT id_config
        INTO v_config_anterior
        FROM medicamentos
        WHERE id_med = NEW.id_med;

        UPDATE medicamentos
        SET id_config = v_config,
            temp_min = NEW.temp_min_med,
            temp_max = NEW.temp_max_med,
            hum_min = NEW.hum_min_med,
            hum_max = NEW.hum_max_med,
            tiempo_gracia_minutos = COALESCE(NEW.tiempo_gracia_minutos, 60)
        WHERE id_med = NEW.id_med;

        CALL actualizar_caducidad(NEW.id_med, NEW.fecha_caducidad);

        UPDATE stock
        SET cantidad = cantidad + NEW.cantidad
        WHERE id_med = NEW.id_med;

        INSERT INTO movimientos_stock (id_med, tipo_mov, cantidad, observaciones)
        VALUES (
            NEW.id_med,
            'ENTRADA',
            NEW.cantidad,
            'Reposicion de stock'
        );

        UPDATE historico_logs
        SET id_config = v_config,
            temp_min = NEW.temp_min_med,
            temp_max = NEW.temp_max_med,
            hum_min = NEW.hum_min_med,
            hum_max = NEW.hum_max_med,
            fecha_caducidad = NEW.fecha_caducidad,
            estado_actual = 'ACTIVO',
            observaciones = 'Medicamento actualizado o repuesto en el sistema'
        WHERE id_med = NEW.id_med
          AND estado_actual <> 'SIN STOCK';

        DELETE FROM alertas
        WHERE id_med = NEW.id_med
          AND tipo_alerta IN ('SIN STOCK', 'STOCK BAJO');

        IF v_config_anterior IS NOT NULL AND v_config_anterior <> v_config THEN
            UPDATE incidencias_medicamento
            SET fecha_fin = CURRENT_TIMESTAMP,
                duracion_minutos = TIMESTAMPDIFF(MINUTE, fecha_inicio, CURRENT_TIMESTAMP),
                estado = 'CERRADA',
                motivo = CONCAT(
                    COALESCE(motivo, ''),
                    ' | Incidencia cerrada automaticamente por cambio de espacio del medicamento'
                )
            WHERE id_med = NEW.id_med
              AND estado = 'ABIERTA';
        END IF;

    END IF;

    -- Si además de insertar/reponer el medicamento llega medición ambiental,
    -- se registra DESPUÉS de insertar/actualizar el medicamento, para que
    -- el control ambiental ya incluya sus límites.
    IF NEW.temperatura_ambiente IS NOT NULL
       AND NEW.humedad_ambiente IS NOT NULL THEN

        CALL registrar_medicion_espacio(
            v_config,
            NEW.temperatura_ambiente,
            NEW.humedad_ambiente
        );

    END IF;

END $$

CREATE TRIGGER control_stock_negativo
BEFORE UPDATE ON stock
FOR EACH ROW
BEGIN
    IF NEW.cantidad < 0 THEN
        SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'La cantidad de stock no puede ser negativa';
    END IF;
END $$

DELIMITER ;

-- =========================================================
-- EVENTO DIARIO PARA REVISAR CADUCIDADES
-- =========================================================

DROP EVENT IF EXISTS revisar_caducidades;

CREATE EVENT revisar_caducidades
ON SCHEDULE EVERY 1 DAY
STARTS CURRENT_TIMESTAMP
ON COMPLETION PRESERVE
DO
    CALL comprobar_caducidad();

-- Si MySQL no te deja ejecutar esta línea por permisos, actívalo desde
-- una cuenta administradora o en la configuración del servidor.
SET GLOBAL event_scheduler = ON;
