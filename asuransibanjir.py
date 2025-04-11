import streamlit as st
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
import zipfile
import os
import folium
from streamlit_folium import folium_static
from folium.plugins import MarkerCluster
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode
import geopandas as gpd
import fiona
import tempfile
import io

st.set_page_config(page_title="Asuransi Banjir Askrindo", page_icon="🏞️" ,layout="centered")
st.title("🌊 Web Application Flood Insurance Askrindo")

# Upload CSV
st.subheader("⬆️ Upload Data yang Diperlukan")
csv_file = st.file_uploader("📄 Upload CSV", type=["csv"])

# Upload beberapa shapefile (format zip)
shp_zips = st.file_uploader("🗂 Upload Beberapa Shapefile (.zip). File zip ini harus terdiri atas .shp, .shx, .dbf, .prj, dsb", type=["zip"], accept_multiple_files=True)

def clean_coordinate_column(series):
    return (
        series.astype(str)
        .str.strip()
        .str.replace("–", "-", regex=False)
        .str.replace(",", ".", regex=False)
        .str.replace(r"[^0-9\.-]", "", regex=True)
    )

if csv_file:
    df = pd.read_csv(csv_file)

    # Ubah kolom tanggal jika perlu
    if 'EXPIRY DATE' in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df['EXPIRY DATE']):
            df['EXPIRY DATE'] = pd.to_datetime(df['EXPIRY DATE'], format='%d/%m/%Y', errors='coerce')

        df['EXPIRY DATE'] = df['EXPIRY DATE'].dt.date
    # Pilihan apakah pakai full data atau inforce data
        st.markdown("### 🔍 Pilih Tipe Data yang Ingin Dipakai")
        data_option = st.radio("Ingin menggunakan data yang mana?", ["Full Data", "Inforce Only (EXPIRY DATE > 31 Des 2024)"])

        if data_option == "Inforce Only (EXPIRY DATE > 31 Des 2024)":
            df = df[df['EXPIRY DATE'] > pd.to_datetime("2024-12-31").date()]
            st.success(f"✅ Menggunakan **data inforce** dengan **{len(df):,} baris** (EXPIRY DATE > 31 Des 2024)")
        else:
            st.success(f"✅ Menggunakan **data full** dengan **{len(df):,} baris**")
    else:
        st.warning("⚠️ Kolom `EXPIRY DATE` tidak ditemukan, tidak bisa filter data inforce.")

    st.dataframe(df, use_container_width=True, hide_index=True)
    
    st.subheader("🪐 Intersection Data dengan Shapefile")
    numeric_cols = df.select_dtypes(include='number').columns.tolist()
    lon_col = st.selectbox("🧭 Pilih kolom Longitude", options=numeric_cols)
    lat_col = st.selectbox("📍 Pilih kolom Latitude", options=numeric_cols)
    

    if 'Latitude' in df.columns and 'Longitude' in df.columns:
        df['Latitude'] = pd.to_numeric(clean_coordinate_column(df['Latitude']), errors='coerce')
        df['Longitude'] = pd.to_numeric(clean_coordinate_column(df['Longitude']), errors='coerce')

        lat_na = df['Latitude'].isna().sum()
        lon_na = df['Longitude'].isna().sum()

        if lat_na > 0 or lon_na > 0:
            st.warning(f"⚠️ Terdapat {lat_na} Latitude dan {lon_na} Longitude yang tidak valid setelah parsing & koreksi.")
            invalid_rows = df[df['Latitude'].isna() | df['Longitude'].isna()]
            st.dataframe(invalid_rows.head())

            invalid_csv = invalid_rows.to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Unduh Baris Tidak Valid", data=invalid_csv, file_name="invalid_coordinates.csv", mime="text/csv")

        df = df.dropna(subset=['Latitude', 'Longitude'])

    if shp_zips and lon_col and lat_col:
        gdf_points = gpd.GeoDataFrame(
            df.copy(),
            geometry=[Point(xy) for xy in zip(df[lon_col], df[lat_col])],
            crs="EPSG:4326"
        )

        joined_list = []

        for shp_zip in shp_zips:
            with tempfile.TemporaryDirectory() as tmpdir:
                with zipfile.ZipFile(shp_zip, 'r') as zip_ref:
                    zip_ref.extractall(tmpdir)

                shp_path = None
                for root, _, files in os.walk(tmpdir):
                    for file in files:
                        if file.endswith(".shp") and not file.startswith("._") and "__MACOSX" not in root:
                            shp_path = os.path.join(root, file)

                if not shp_path:
                    st.warning(f"Tidak ditemukan file .shp dalam ZIP: {shp_zip.name}")
                    continue

                try:
                    gdf_shape = gpd.read_file(shp_path)
                    gdf_points_proj = gdf_points.to_crs(gdf_shape.crs)

                    joined = gpd.sjoin(gdf_points_proj, gdf_shape, how="left", predicate="intersects")
                    joined_list.append(joined)

                except Exception as e:
                    st.error(f"Gagal memproses shapefile dari {shp_zip.name}: {e}")

        if joined_list:
            combined = pd.concat(joined_list)

            keywords = ['gridcode', 'hasil_gridcode', 'kode_grid']
            gridcode_cols = [col for col in combined.columns if any(kw in col.lower() for kw in keywords)]
            grid_col = gridcode_cols[0] if gridcode_cols else None

            if grid_col:
                combined = combined[[lon_col, lat_col, grid_col]].drop_duplicates(subset=[lon_col, lat_col])
            else:
                combined = combined[[lon_col, lat_col]].drop_duplicates()

            final = df.merge(combined, on=[lon_col, lat_col], how='left')

            if grid_col:
                st.subheader("🧐 Interpretasikan gridcode Menjadi Kelas Risiko")
                selected_gridcode = st.selectbox("📊 Pilih kolom gridcode untuk dikategorikan:", options=gridcode_cols)
                final['Kategori Risiko'] = final[selected_gridcode].map({1: 'Rendah', 2: 'Sedang', 3: 'Tinggi'}).fillna("No Risk")
                st.dataframe(final[[selected_gridcode, 'Kategori Risiko']], use_container_width=True, hide_index=True)
                st.info(f"🔍 Data akhir (termasuk yang tidak memiliki intersection di .shp) memiliki **{len(final):,} baris.**")
            else:
                st.warning("⚠️ Tidak ditemukan kolom terkait 'gridcode'. Tidak dapat mengkategorikan risiko.")

            st.subheader("🧮 Menghitung Rate Berdasarkan Kategori Risiko dan Okupasi")
            st.markdown("""
            <div style='text-align: justify'>
            Kategori Okupasi dibedakan menjadi Residensial, Industrial dan Komersial. Selain itu, Kategori Risiko akan memuat jumlah lantai dari bangunan. Untuk mengetahui acuan rate yang digunakan oleh Askrindo, maka dapat dilihat melalui tabel berikut.
            </div>
            """, unsafe_allow_html=True)
            data = {
            "Kategori Utama": ["Building"] * 4 + ["Content/Stock"] * 3 + ["Machine"] * 3,
            "Kategori": [
            "No Risk", "Rendah (s.d. 0.75M)", "Sedang (>0.75M - 1.5M)", "Tinggi (>1.5M)",
            "Rendah (≤0.75M)", "Sedang (>0.75M - 1.5M)", "Tinggi (>1.5M)",
            "Rendah (≤0.75M)", "Sedang (>0.75M - 1.5M)", "Tinggi (>1.5M)"
            ],
            "Residential 1 lantai": ["0%", "15%", "30%", "50%", "20%", "40%", "60%", "10%", "25%", "45%"],
            "Residential >1 lantai": ["0%", "10%", "20%", "35%", "15%", "30%", "45%", "8%", "20%", "35%"],
            "Commercial 1 lantai": ["0%", "20%", "35%", "55%", "30%", "50%", "70%", "15%", "35%", "55%"],
            "Commercial >1 lantai": ["0%", "15%", "25%", "40%", "25%", "40%", "55%", "12%", "30%", "45%"],
            "Industrial 1 lantai": ["0%", "10%", "20%", "40%", "15%", "30%", "50%", "25%", "50%", "70%"],
            "Industrial >1 lantai": ["0%", "8%", "15%", "30%", "10%", "25%", "40%", "20%", "40%", "60%"]
            }

            df_kategori = st.dataframe(data, use_container_width=True, hide_index=True)
            text_columns = final.select_dtypes(include='object').columns.tolist()

            if 'Kategori Risiko' in final.columns:
                building_col = st.selectbox("🏢 Pilih kolom Kategori Okupasi:", options=text_columns)
                floor_col = st.selectbox("📏 Pilih kolom Jumlah Lantai:", options=text_columns)

                rate_dict = {
                'No Risk': {
                'Residensial': {'1': 0.0, 'Lebih dari 1 Lantai': 0.0},
                'Komersial': {'1': 0.0, 'Lebih dari 1 Lantai': 0.0},
                'Industrial': {'1': 0.0, 'Lebih dari 1 Lantai': 0.0}
                },
                'Rendah': {
                'Residensial': {'1': 0.15, 'Lebih dari 1 Lantai': 0.10},
                'Komersial': {'1': 0.20, 'Lebih dari 1 Lantai': 0.15},
                'Industrial': {'1': 0.10, 'Lebih dari 1 Lantai': 0.08}
                },
                'Sedang': {
                'Residensial': {'1': 0.30, 'Lebih dari 1 Lantai': 0.20},
                'Komersial': {'1': 0.35, 'Lebih dari 1 Lantai': 0.25},
                'Industrial': {'1': 0.20, 'Lebih dari 1 Lantai': 0.15}
                },
                'Tinggi': {
                'Residensial': {'1': 0.50, 'Lebih dari 1 Lantai': 0.35},
                'Komersial': {'1': 0.55, 'Lebih dari 1 Lantai': 0.40},
                'Industrial': {'1': 0.40, 'Lebih dari 1 Lantai': 0.30}
                }
            }

                def lookup_rate(row):
                    try:
                        return rate_dict[row['Kategori Risiko']][row[building_col]][row[floor_col]]
                    except:
                        return None

                final['Rate'] = final.apply(lookup_rate, axis=1)

                st.dataframe(final[['Kategori Risiko', building_col, floor_col, 'Rate']], use_container_width=True, hide_index=True)
                st.success(f"Data berhasil dimuat disertai rate sebanyak **{len(df):,} baris valid**.")

            cols_to_show = df.columns.tolist() + ['Kategori Risiko', 'Rate']
            cols_to_show = [col for col in cols_to_show if col in final.columns]

        else:
            st.warning("⚠️ Tidak ada shapefile yang berhasil diproses.")

        st.markdown("### 💰 Hitung Probable Maximum Losses (PML)")

        rate_cols = [col for col in final.columns if 'rate' in col.lower()]
        if rate_cols:
            selected_rate = st.selectbox("📈 Pilih kolom Rate", options=rate_cols)
            numeric_or_mixed_cols = final.columns.tolist()
            selected_tsi = st.selectbox("💵 Pilih kolom TSI", options=numeric_or_mixed_cols)

        # Bersihkan kolom TSI jadi angka
        def clean_tsi_column(series):
            return pd.to_numeric(
                series.astype(str)
                  .str.replace(r"[^\d]", "", regex=True),  # hapus semua kecuali digit
            errors='coerce'
        )
        
        final[selected_tsi] = clean_tsi_column(final[selected_tsi])

        final['PML'] = final[selected_tsi] * final[selected_rate]

        st.dataframe(final[[selected_tsi, selected_rate, 'PML']], use_container_width=True, hide_index=True)

        output_premi = io.BytesIO()
        final.to_excel(output_premi, index=False, engine='openpyxl')
        st.download_button("⬇️ Unduh Data dengan PML", data=output_premi.getvalue(),
                       file_name="DataBanjirAskrindo_Computated.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        if lon_col and lat_col and not final.empty:
                st.subheader("🌐 Peta Titik Koordinat")

            # Buat peta dengan titik tengah berdasarkan rata-rata lokasi
        map_center = [final[lat_col].mean(), final[lon_col].mean()]
        m = folium.Map(location=map_center, zoom_start=9)
        marker_cluster = MarkerCluster().add_to(m)

        # Tambahkan titik ke peta
        for _, row in final.iterrows():
                popup_content = "<br>".join([
                    f"<b>{col}</b>: {row[col]}" if pd.notnull(row[col]) else f"<b>{col}</b>: -"
                    for col in final.columns
                ])
                folium.CircleMarker(
                location=[row[lat_col], row[lon_col]],
                radius=4,
                color="blue",
                fill=True,
                fill_opacity=0.6,
                popup=folium.Popup(popup_content, max_width=300, auto_pan = True)
            ).add_to(marker_cluster)
        
        # Tampilkan peta di Streamlit
        folium_static(m, width=2000, height=1000)
        
        st.markdown("## 📊 Ringkasan Hasil")

        # 1. Total data
        st.write(f"**Jumlah Data:** {len(final):,}")

        # 2. Distribusi Kategori Risiko (kalau ada)
        if 'Kategori Risiko' in final.columns:
            st.write("**Distribusi Kategori Risiko:**")
            st.dataframe(final['Kategori Risiko'].value_counts().rename_axis('Kategori').reset_index(name='Jumlah'), use_container_width=True, hide_index=True)
        
        if 'UY' in final.columns:
            st.markdown("### 📋 Ringkasan Berdasarkan Underwriting Year (UY)")

        summary_uy = final.groupby('UY').agg(
        Jumlah_Polis=('UY', 'count'),
        TotalTSI=(selected_tsi, 'sum'),
        TotalPML=('PML', 'sum')
        ).reset_index().rename(columns={
        'Jumlah_Polis': 'Jumlah Polis',
        'TotalTSI': 'Total TSI',
        'TotalPML': 'Total PML'
        })

        st.dataframe(summary_uy.style.format({
        'Total TSI': '{:2e}',
        'Total PML': '{:2e}',
            }), use_container_width=True, hide_index=True)
                 
        import altair as alt

        # Line chart untuk Total TSI dan Total PML per UY
        summary_melted = summary_uy.melt(id_vars='UY', value_vars=['Total TSI', 'Total PML'],
                                 var_name='Tipe', value_name='Nilai')

        chart = alt.Chart(summary_melted).mark_line(point=True).encode(
        x='UY:O',
        y=alt.Y('Nilai:Q', title='Nilai (Rp)', axis=alt.Axis(format='e')),
        color=alt.Color('Tipe:N', title='Jenis Nilai',
                    scale=alt.Scale(range=['#66a3ff', '#f08522'])),
        tooltip=['UY', 'Tipe', alt.Tooltip('Nilai:Q', title='Nilai (Rp)', format='e')]
        ).properties(
        title='📈 Tren Total TSI dan PML per UY',
        width=700,
        height=400
        ).interactive()

        st.altair_chart(chart, use_container_width=True)

        if 'Kategori Okupasi' in final.columns:
            st.markdown("### 📋 Ringkasan per Kategori Okupasi")

        summary_okupasi = final.groupby('Kategori Okupasi').agg(
        jml_polis=('Kategori Okupasi', 'count'),
        total_tsi=(selected_tsi, 'sum'),
        total_pml=('PML', 'sum')
        ).reset_index().rename(columns={
        'jml_polis': 'Jumlah Polis',
        'total_tsi': 'Total TSI',
        'total_pml': 'Total PML'
        })

        summary_okupasi['Total TSI'] = summary_okupasi['Total TSI'].apply(lambda x: f"{x:.2e}")
        summary_okupasi['Total PML'] = summary_okupasi['Total PML'].apply(lambda x: f"{x:.2e}")

        st.dataframe(summary_okupasi, use_container_width=True, hide_index=True)

        # Melt untuk visualisasi
        summary_melted = summary_okupasi.melt(
        id_vars='Kategori Okupasi',
        value_vars=['Total TSI', 'Total PML'],
        var_name='Tipe',
        value_name='Nilai'
        )

        # Stacked Bar Chart
        chart = alt.Chart(summary_melted).mark_bar().encode(
        x=alt.X('Kategori Okupasi:N', title='Kategori Okupasi'),
        y=alt.Y('Nilai:Q', title='Nilai (Rp)', stack='zero', axis=alt.Axis(format='e')),
        color=alt.Color('Tipe:N', title='Jenis Nilai',
                    scale=alt.Scale(range=['#66a3ff', '#f08522'])),
        tooltip=['Kategori Okupasi', 'Tipe', alt.Tooltip('Nilai:Q', format=',')]
        ).properties(
        title='📊 Distribusi Total TSI dan PML per Kategori Okupasi',
        width=700,
        height=400
        )

        st.altair_chart(chart, use_container_width=True)

        if 'Kategori Risiko' in final.columns:
            st.markdown("### 📋 Ringkasan Berdasarkan Kategori Risiko")

        summary_riskclass = final.groupby('Kategori Risiko').agg(
        Jumlah_Polis=('Kategori Risiko', 'count'),
        TotalTSI=(selected_tsi, 'sum'),
        TotalPML=('PML', 'sum')
        ).reset_index().rename(columns={
        'Jumlah_Polis': 'Jumlah Polis',
        'TotalTSI': 'Total TSI',
        'TotalPML': 'Total PML'
        })

        summary_riskclass['Total TSI'] = summary_riskclass['Total TSI'].apply(lambda x: f"{x:.2e}")
        summary_riskclass['Total PML'] = summary_riskclass['Total PML'].apply(lambda x: f"{x:.2e}")

        st.dataframe(summary_riskclass, use_container_width=True, hide_index=True)

        st.markdown("### 📋 Ringkasan Berdasarkan UY dan Kategori Risiko")
        # Step 1: Group and aggregate
        summary = final.groupby(['UY', 'Kategori Risiko']).agg(
        Count_Polis=('Kategori Risiko', 'count'),
        Sum_TSI=(selected_tsi, 'sum'),
        Estimated_Claim=('PML', 'sum')  # ganti nama kolom kalau perlu
        ).reset_index().rename(columns={
            'Count_Polis': 'Jumlah Polis',
            'Sum_TSI': 'Total TSI',
            'Estimated_Claim' : 'PML'
        })

        # Step 2: Pivot jadi MultiIndex column table
        pivoted = summary.pivot(index='UY', columns='Kategori Risiko')
        pivoted = pivoted.fillna(0)

        # Step 3: Rapikan nama kolom multi-level
        pivoted.columns = [' '.join(col).strip() for col in pivoted.columns.values]

        # Step 4: Tampilkan
        styled_df = pivoted.applymap(lambda x: f"{int(x):,}".replace(",", "."))

        
        st.dataframe(styled_df, use_container_width=True, hide_index=True)

        st.markdown("### 📋 Ringkasan Berdasarkan UY, Kategori Risiko dan Okupasi")
        # Jumlah polis
        count_polis = final.pivot_table(
        index='UY',
        columns=['Kategori Okupasi', 'Kategori Risiko'],
        aggfunc='size'  # jumlah baris mewakili jumlah polis
        ).fillna(0).astype(int)

        # Sum TSI
        sum_tsi = final.pivot_table(
        index='UY',
        columns=['Kategori Okupasi', 'Kategori Risiko'],
        values=selected_tsi,  
        aggfunc='sum'
        ).fillna(0).astype(int)

        # Estimated Claim
        est_claim = final.pivot_table(
        index='UY',
        columns=['Kategori Okupasi', 'Kategori Risiko'],
        values='PML',  
        aggfunc='sum'
        ).fillna(0).astype(int)
        
        # Formatting angka dengan titik ribuan
        def format_ribuan(df):
            return df.applymap(lambda x: f"{x:,}".replace(",", "."))

        st.markdown("#### Count Polis")
        st.dataframe(count_polis)

        st.markdown("#### Sum TSI")
        st.dataframe(format_ribuan(sum_tsi))

        st.markdown("#### Probable Maximum Loss")
        st.dataframe(format_ribuan(est_claim))
else:
    st.warning("⚠️ Kolom rate belum tersedia. Pastikan rate sudah dihitung sebelumnya.")