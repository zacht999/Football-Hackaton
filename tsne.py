import pandas as pd
import numpy as np
from sklearn.manifold import TSNE
import plotly.express as px
from google.cloud import bigquery
import dash
from dash import dcc, html, dash_table
from dash.dependencies import Input, Output, State, ALL
import datetime
import json
from scipy.stats import spearmanr
import plotly.graph_objs as go
from plotly.subplots import make_subplots
from dash import callback_context as ctx
import vertexai
from vertexai.preview.generative_models import GenerativeModel
import dash_bootstrap_components as dbc
import time
from dash.exceptions import PreventUpdate

# Connect to BigQuery and fetch data
client = bigquery.Client()

# Team query
team_query = """
WITH player_team_mapping AS (
    SELECT DISTINCT team.name AS team_name, player.name AS player_name
    FROM `statsbomb.events`
),
player_embeddings AS (
    SELECT player_name, ml_generate_embedding_result AS embedding
    FROM `statsbomb.player_embeddings`
),
team_player_embeddings AS (
    SELECT 
      ptm.team_name,
      AVG(e) AS avg_embedding
    FROM player_team_mapping ptm
    JOIN player_embeddings pe ON ptm.player_name = pe.player_name,
    UNNEST(pe.embedding) AS e WITH OFFSET
    GROUP BY ptm.team_name, OFFSET
),
team_stats AS (
    SELECT
        team.name AS team_name,
        AVG(CASE WHEN type.name = 'Pass' THEN 1 ELSE 0 END) AS pass_ratio,
        AVG(CASE WHEN type.name = 'Shot' THEN 1 ELSE 0 END) AS shot_ratio,
        AVG(CASE WHEN type.name = 'Ball Recovery' THEN 1 ELSE 0 END) AS ball_recovery_ratio,
        AVG(CASE WHEN type.name = 'Duel' THEN 1 ELSE 0 END) AS duel_ratio,
        AVG(CASE WHEN type.name = 'Interception' THEN 1 ELSE 0 END) AS interception_ratio,
        AVG(CASE WHEN shot.outcome.name = 'Goal' THEN 1 ELSE 0 END) AS goal_ratio,
        AVG(CASE WHEN type.name = 'Pressure' THEN 1 ELSE 0 END) AS pressure_ratio,
        AVG(CASE WHEN type.name = 'Dribble' THEN 1 ELSE 0 END) AS dribble_ratio,
        AVG(CASE WHEN type.name = 'Foul Committed' THEN 1 ELSE 0 END) AS foul_committed_ratio,
        AVG(CASE WHEN type.name = 'Foul Won' THEN 1 ELSE 0 END) AS foul_won_ratio,
        AVG(CASE WHEN type.name = 'Carry' THEN 1 ELSE 0 END) AS carry_ratio,
        AVG(CASE WHEN type.name = 'Dispossessed' THEN 1 ELSE 0 END) AS dispossessed_ratio,
        AVG(CASE WHEN type.name = 'Clearance' THEN 1 ELSE 0 END) AS clearance_ratio,
        AVG(CASE WHEN type.name = 'Block' THEN 1 ELSE 0 END) AS block_ratio
    FROM `statsbomb.events`
    GROUP BY team.name
)
SELECT 
    tpe.team_name,
    ARRAY_AGG(tpe.avg_embedding) AS team_embedding,
    ts.pass_ratio,
    ts.shot_ratio,
    ts.ball_recovery_ratio,
    ts.duel_ratio,
    ts.interception_ratio,
    ts.goal_ratio,
    ts.pressure_ratio,
    ts.dribble_ratio,
    ts.foul_committed_ratio,
    ts.foul_won_ratio,
    ts.carry_ratio,
    ts.dispossessed_ratio,
    ts.clearance_ratio,
    ts.block_ratio
FROM team_player_embeddings tpe
JOIN team_stats ts ON tpe.team_name = ts.team_name
GROUP BY 
    tpe.team_name,
    ts.pass_ratio,
    ts.shot_ratio,
    ts.ball_recovery_ratio,
    ts.duel_ratio,
    ts.interception_ratio,
    ts.goal_ratio,
    ts.pressure_ratio,
    ts.dribble_ratio,
    ts.foul_committed_ratio,
    ts.foul_won_ratio,
    ts.carry_ratio,
    ts.dispossessed_ratio,
    ts.clearance_ratio,
    ts.block_ratio
"""

# Player query
player_query = """
WITH player_info AS (
    SELECT DISTINCT team.name AS team_name, player.name as player_name, position.name as position
    FROM `statsbomb.events`
),
player_stats AS (
    SELECT 
        player_name,
        pass_ratio,
        shot_ratio,
        ball_recovery_ratio,
        duel_ratio,
        interception_ratio,
        goal_ratio,
        pressure_ratio,
        dribble_ratio,
        foul_committed_ratio,
        foul_won_ratio,
        carry_ratio,
        dispossessed_ratio,
        clearance_ratio,
        block_ratio
    FROM `statsbomb.vw_player_stats_for_embeddings`
)
SELECT 
    pe.player_name, 
    pe.ml_generate_embedding_result AS embedding, 
    pi.team_name, 
    pi.position,
    ps.*
FROM `statsbomb.player_embeddings` pe
JOIN player_info pi ON pe.player_name = pi.player_name
JOIN player_stats ps ON pe.player_name = ps.player_name
"""

team_df = client.query(team_query).to_dataframe()
player_df = client.query(player_query).to_dataframe()

# Extract embeddings
team_df['team_embedding'] = team_df['team_embedding'].apply(lambda x: json.loads(x) if isinstance(x, str) else x)
team_embeddings = np.array(team_df['team_embedding'].tolist())
player_embeddings = np.array(player_df['embedding'].tolist())

# Calculate the number of teams
n_teams = len(team_df)

# Set the maximum perplexity for teams
max_perplexity_teams = min(30, n_teams - 1)

# Initialize the Dash app
external_stylesheets = ['https://codepen.io/chriddyp/pen/bWLwgP.css']
app = dash.Dash(__name__, title="Football t-SNE", external_stylesheets=external_stylesheets, suppress_callback_exceptions=True)

current_year = datetime.datetime.now().year

# Define the layout
app.layout = html.Div([
    dcc.Store(id='filtered-data'),  # Add this line
    dcc.Store(id='filtered-team-data'),  # Add this line
    html.Link(
        rel='stylesheet',
        href='https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Montserrat:wght@700&display=swap'
    ),
    html.Div([
        html.Div([
            html.H1("Visualizing Embeddings using a t-SNE map", style={
                'font-family': 'Montserrat', 
                'color': 'white',
                'text-align': 'center',
                'font-weight': '700',
                'margin': '0',
                'padding': '20px',
            })
        ], style={
            'background-color': '#4285F4',
            'border-radius': '20px',
            'margin-bottom': '20px',
        }),
        
        html.Div([
            # Team View
            html.Div([
                html.H2("Team View", style={'font-family': 'Montserrat', 'color': '#5B9BFF', 'font-weight': '700'}),
                html.Div([
                    html.Details([
                        html.Summary("Adjust t-SNE map settings", style={
                            'cursor': 'pointer',
                            'font-family': 'Montserrat',
                            'font-weight': '700',
                            'color': '#4285F4',
                            'margin-bottom': '10px',
                            'font-size': '18px'
                        }),
                        html.Div([
                            html.Label("Perplexity:", style={'font-family': 'Inter', 'font-weight': '500', 'margin-right': '10px'}),
                            dcc.Slider(
                                id='team-perplexity-slider',
                                min=5,
                                max=max_perplexity_teams,
                                step=1,
                                value=min(30, max_perplexity_teams),
                                marks=None,
                                tooltip={"placement": "bottom", "always_visible": True},
                                className='custom-slider'
                            ),
                        ], style={'margin-bottom': '10px'}),
                        html.Div([
                            html.Label("Iterations:", style={'font-family': 'Inter', 'font-weight': '500', 'margin-right': '10px'}),
                            dcc.Slider(
                                id='team-iterations-slider',
                                min=100,
                                max=1000,
                                step=100,
                                value=700,
                                marks=None,
                                tooltip={"placement": "bottom", "always_visible": True},
                                className='custom-slider'
                            ),
                        ])
                    ], style={
                        'backgroundColor': '#f1f3f4',
                        'padding': '10px',
                        'borderRadius': '20px',
                        'marginBottom': '10px'
                    })
                ], style={
                    'backgroundColor': '#f1f3f4',
                    'padding': '10px',
                    'borderRadius': '20px',
                    'marginBottom': '10px'
                }),
                html.Div([
                    dcc.Dropdown(
                        id='team-select',
                        options=[{'label': team, 'value': team} for team in sorted(team_df['team_name'].unique())],
                        multi=True,
                        placeholder="Select team(s)",
                        searchable=True,
                        clearable=True
                    ),
                ], style={'margin-bottom': '10px'}),
                dcc.Loading(
                    id="loading-team",
                    type="default",
                    children=[dcc.Graph(id='team-tsne-plot')]
                ),
                html.Div([
                    html.Details([
                        html.Summary("Team Feature Importance", style={
                            'cursor': 'pointer',
                            'font-family': 'Montserrat',
                            'font-weight': '700',
                            'color': '#4285F4',
                            'margin-bottom': '10px',
                            'font-size': '18px'
                        }),
                        html.Div(id='team-feature-importance')
                    ], style={
                        'margin-top': '20px',
                        'backgroundColor': '#f1f3f4',
                        'padding': '10px',
                        'borderRadius': '20px',
                        'marginBottom': '10px'
                    })
                ], className='feature-importance-box'),
                dcc.Loading(
                    id="loading-team-insights",
                    type="default",
                    children=[html.Div(id='team-insights', style={'margin-top': '20px'})]
                ),
            ], style={'width': '48%', 'display': 'inline-block', 'vertical-align': 'top'}),

            # Player View
            html.Div([
                html.Div(id='player-view-container', children=[
                    html.H2("Player View", style={'font-family': 'Montserrat', 'color': '#5B9BFF', 'font-weight': '700'}),
                    html.Div([
                        html.Details([
                            html.Summary("Adjust t-SNE map settings", style={
                                'cursor': 'pointer',
                                'font-family': 'Montserrat',
                                'font-weight': '700',
                                'color': '#4285F4',
                                'margin-bottom': '10px',
                                'font-size': '18px'
                            }),
                            html.Div([
                                html.Label("Perplexity:", style={'font-family': 'Inter', 'font-weight': '500', 'margin-right': '10px'}),
                                dcc.Slider(
                                    id='player-perplexity-slider',
                                    min=5,
                                    max=100,
                                    step=5,
                                    value=45,
                                    marks=None,
                                    tooltip={"placement": "bottom", "always_visible": True},
                                    className='custom-slider'
                                ),
                            ], style={'margin-bottom': '10px'}),
                            html.Div([
                                html.Label("Iterations:", style={'font-family': 'Inter', 'font-weight': '500', 'margin-right': '10px'}),
                                dcc.Slider(
                                    id='player-iterations-slider',
                                    min=100,
                                    max=1000,
                                    step=100,
                                    value=700,
                                    marks=None,
                                    tooltip={"placement": "bottom", "always_visible": True},
                                    className='custom-slider'
                                ),
                            ])
                        ], style={
                            'backgroundColor': '#f1f3f4',
                            'padding': '10px',
                            'borderRadius': '20px',
                            'marginBottom': '10px'
                        })
                    ], style={
                        'backgroundColor': '#f1f3f4',
                        'padding': '10px',
                        'borderRadius': '20px',
                        'marginBottom': '10px'
                    }),
                    html.Div([
                        html.Div([
                            dcc.Dropdown(
                                id='player-select',
                                multi=True,
                                placeholder="Select player(s)"
                            ),
                        ], style={'width': '48%', 'display': 'inline-block', 'marginRight': '4%'}),
                        html.Div([
                            dcc.Dropdown(
                                id='position-filter',
                                multi=True,
                                placeholder="Select positions"
                            ),
                        ], style={'width': '48%', 'display': 'inline-block'}),
                    ], style={'marginBottom': '10px'}),
                    dcc.Loading(
                        id="loading-player",
                        type="default",
                        children=[dcc.Graph(id='player-tsne-plot')]
                    ),
                    html.Div([
                        html.Details([
                            html.Summary("Player Feature Importance", style={
                                'cursor': 'pointer',
                                'font-family': 'Montserrat',
                                'font-weight': '700',
                                'color': '#4285F4',
                                'margin-bottom': '10px',
                                'font-size': '18px'
                            }),
                            html.Div(id='feature-importance')
                        ], style={
                            'margin-top': '20px',
                            'backgroundColor': '#f1f3f4',
                            'padding': '10px',
                            'borderRadius': '20px',
                            'marginBottom': '10px'
                        })
                    ], className='feature-importance-box'),
                    dcc.Loading(
                        id="loading-player-insights",
                        type="default",
                        children=[html.Div(id='player-insights', style={'margin-top': '20px'})]
                    ),
                ]),
                html.Div(id='player-view-message', style={'display': 'none'})
            ], style={'width': '48%', 'display': 'inline-block', 'vertical-align': 'top', 'margin-left': '4%'}),
        ], style={'display': 'flex', 'alignItems': 'flex-start'}),  # Add this style to align the two views
    ], style={
        'max-width': '1800px',
        'margin': '0 auto',
        'padding': '20px',
        'font-family': 'Inter, sans-serif',
        'background-color': '#ffffff',
        'box-shadow': '0 1px 2px 0 rgba(60,64,67,0.3), 0 1px 3px 1px rgba(60,64,67,0.15)',
        'border-radius': '20px'
    }),

    # Credits
    html.Div(
        f"{datetime.datetime.now().year} | Made with ♥ by andrewankenobi@google.com | powered by Google Cloud",
        style={
            'position': 'fixed',
            'bottom': '0',
            'left': '50%',
            'transform': 'translateX(-50%)',
            'background-color': '#f1f3f4',
            'padding': '10px 20px',
            'border-radius': '10px 10px 0 0',
            'font-family': 'Inter, sans-serif',
            'font-size': '14px',
            'color': '#333',
            'box-shadow': '0 -2px 4px rgba(0, 0, 0, 0.1)',
            'z-index': '1000'
        }
    )
], style={
    'background': 'linear-gradient(to bottom, #5B9BFF, #4285F4)',
    'min-height': '100vh',
    'padding': '20px'
})

# Define feature columns
feature_columns = ['pass_ratio', 'shot_ratio', 'ball_recovery_ratio', 'duel_ratio', 'interception_ratio',
                   'goal_ratio', 'pressure_ratio', 'dribble_ratio', 'foul_committed_ratio', 'foul_won_ratio',
                   'carry_ratio', 'dispossessed_ratio', 'clearance_ratio', 'block_ratio']

# Modify the team plot callback
@app.callback(
    [Output('team-tsne-plot', 'figure'),
     Output('filtered-team-data', 'data'),
     Output('team-insights', 'children', allow_duplicate=True)],
    [Input('team-perplexity-slider', 'value'),
     Input('team-iterations-slider', 'value'),
     Input('team-select', 'value')],
    prevent_initial_call='initial_duplicate'
)
def update_team_plot(perplexity, n_iter, selected_teams):
    global feature_columns
    
    tsne = TSNE(n_components=3, perplexity=min(perplexity, n_teams - 1), n_iter=n_iter, random_state=42)
    tsne_results = tsne.fit_transform(team_embeddings)
    
    team_df['x'] = tsne_results[:, 0]
    team_df['y'] = tsne_results[:, 1]
    team_df['z'] = tsne_results[:, 2]
    
    # Create 3D scatter plot
    scatter_fig = px.scatter_3d(team_df, x='x', y='y', z='z', hover_name='team_name')
    scatter_fig.update_traces(marker=dict(size=5, color='#4285F4'))  # Google Blue
    scatter_fig.update_layout(
        height=750,
        font_family='Inter',
        title_font_family='Montserrat',
        paper_bgcolor='#ffffff',
        plot_bgcolor='#CEDAEC',
        scene=dict(
            xaxis=dict(showgrid=False, zeroline=False),
            yaxis=dict(showgrid=False, zeroline=False),
            zaxis=dict(showgrid=False, zeroline=False),
        ),
        margin=dict(l=0, r=0, t=40, b=0)
    )
    
    if selected_teams:
        team_data = team_df[team_df['team_name'].isin(selected_teams)]
        highlight_trace = px.scatter_3d(team_data, x='x', y='y', z='z', hover_name='team_name',
                                        color_discrete_sequence=['#EA4335']).data[0]  # Google Red
        highlight_trace.update(marker=dict(size=10))
        scatter_fig.add_trace(highlight_trace)
    
    # Disable click events on the plot
    scatter_fig.update_layout(clickmode='none')
    
    # Generate insights
    insights_data = team_df[['team_name', 'x', 'y', 'z'] + feature_columns].to_dict('records')
    insights = generate_insights(insights_data, selected_teams, "team", perplexity, n_iter)
    insights_div = html.Div([
        dcc.Markdown(insights, dangerously_allow_html=True)
    ])
    
    return scatter_fig, team_df.to_dict('records'), insights_div

# Modify the player plot callback
@app.callback(
    [Output('player-tsne-plot', 'figure'),
     Output('filtered-data', 'data'),
     Output('player-insights', 'children')],
    [Input('team-select', 'value'),
     Input('player-perplexity-slider', 'value'),
     Input('player-iterations-slider', 'value'),
     Input('player-select', 'value'),
     Input('position-filter', 'value')],
    [State('player-tsne-plot', 'figure')],
    prevent_initial_call=True
)
def update_player_plot(selected_teams, perplexity, n_iter, selected_players, selected_positions, current_figure):
    triggered = [t['prop_id'] for t in ctx.triggered]
    
    if 'team-select.value' in triggered or 'player-perplexity-slider.value' in triggered or 'player-iterations-slider.value' in triggered:
        # Recalculate t-SNE if teams or t-SNE parameters changed
        if not selected_teams:
            return go.Figure(), [], None

        global feature_columns, player_df
        
        filtered_df = player_df[player_df['team_name'].isin(selected_teams)].copy()
        
        # Perform t-SNE on the filtered player data
        tsne = TSNE(n_components=3, perplexity=min(perplexity, len(filtered_df) - 1), n_iter=n_iter, random_state=42)
        tsne_results = tsne.fit_transform(filtered_df[feature_columns].values)
        
        filtered_df['x'] = tsne_results[:, 0]
        filtered_df['y'] = tsne_results[:, 1]
        filtered_df['z'] = tsne_results[:, 2]
        
        fig = go.Figure(data=[go.Scatter3d(
            x=filtered_df['x'],
            y=filtered_df['y'],
            z=filtered_df['z'],
            mode='markers',
            marker=dict(
                size=5,
                color='#4285F4',  # Google Blue
                opacity=0.8
            ),
            text=filtered_df['player_name'] + '<br>' + filtered_df['position'] + '<br>' + filtered_df['team_name'],
            hoverinfo='text'
        )])

        fig.update_layout(
            height=750,
            font_family='Inter',
            title_font_family='Montserrat',
            paper_bgcolor='#ffffff',
            plot_bgcolor='#CEDAEC',
            scene=dict(
                xaxis=dict(showgrid=False, zeroline=False, visible=True, title=''),
                yaxis=dict(showgrid=False, zeroline=False, visible=True, title=''),
                zaxis=dict(showgrid=False, zeroline=False, visible=True, title=''),
            ),
            margin=dict(l=0, r=0, t=40, b=0)
        )
        
        # Generate insights
        insights_data = filtered_df[['player_name', 'team_name', 'position'] + feature_columns].to_dict('records')
        insights = generate_insights(insights_data, None, "player", perplexity, n_iter)
        insights_div = html.Div([
            dcc.Markdown(insights, dangerously_allow_html=True)
        ])
        
        return fig, filtered_df.to_dict('records'), insights_div
    
    else:
        # Only highlight players/positions if those inputs changed
        if not current_figure:
            raise PreventUpdate

        # Extract data from the current figure
        data = current_figure['data'][0]
        df = pd.DataFrame({
            'x': data['x'],
            'y': data['y'],
            'z': data['z'],
            'text': data['text']
        })
        
        # Create a copy of the current figure
        fig = go.Figure(current_figure)

        # Remove any existing highlight traces
        fig.data = [trace for trace in fig.data if trace.name not in ['Highlighted Players', 'Highlighted Positions']]

        # Update the base trace
        base_trace = fig.data[0]
        base_trace.marker.size = 5
        base_trace.marker.opacity = 0.5
        base_trace.marker.color = '#4285F4'  # Google Blue

        # Highlight selected players
        if selected_players:
            player_data = df[df['text'].str.contains('|'.join(selected_players), case=False)]
            if not player_data.empty:
                highlight_trace = go.Scatter3d(
                    x=player_data['x'], y=player_data['y'], z=player_data['z'],
                    mode='markers',
                    marker=dict(
                        size=10,
                        color='#EA4335',  # Google Red
                        symbol='diamond',
                        line=dict(width=2, color='#FFFFFF'),  # White border
                        opacity=1
                    ),
                    name='Highlighted Players',
                    text=player_data['text'],
                    hoverinfo='text'
                )
                fig.add_trace(highlight_trace)

        # Highlight selected positions
        if selected_positions:
            position_data = df[df['text'].str.contains('|'.join(selected_positions), case=False)]
            if not position_data.empty:
                highlight_trace = go.Scatter3d(
                    x=position_data['x'], y=position_data['y'], z=position_data['z'],
                    mode='markers',
                    marker=dict(
                        size=8,
                        color='#FBBC05',  # Google Yellow
                        symbol='circle',
                        line=dict(width=2, color='#FFFFFF'),  # White border
                        opacity=1
                    ),
                    name='Highlighted Positions',
                    text=position_data['text'],
                    hoverinfo='text'
                )
                fig.add_trace(highlight_trace)

        return fig, dash.no_update, dash.no_update

# Modify the feature importance callback
@app.callback(
    Output('feature-importance', 'children'),
    [Input('filtered-data', 'data')]
)
def update_feature_importance(filtered_data):
    global feature_columns
    # Convert the filtered data back to a dataframe
    filtered_df = pd.DataFrame(filtered_data)

    if filtered_df.empty:
        return html.Div("No data available for feature importance calculation.")

    # Extract t-SNE coordinates from the filtered dataframe
    x = filtered_df['x']
    y = filtered_df['y']
    z = filtered_df['z']

    # Calculate correlations between original features and t-SNE coordinates
    feature_columns = ['pass_ratio', 'shot_ratio', 'ball_recovery_ratio', 'duel_ratio', 'interception_ratio',
                       'goal_ratio', 'pressure_ratio', 'dribble_ratio', 'foul_committed_ratio', 'foul_won_ratio',
                       'carry_ratio', 'dispossessed_ratio', 'clearance_ratio', 'block_ratio']
    
    correlations = []
    for feature in feature_columns:
        corr_x, _ = spearmanr(filtered_df[feature], x)
        corr_y, _ = spearmanr(filtered_df[feature], y)
        corr_z, _ = spearmanr(filtered_df[feature], z)
        correlations.append((feature, max(abs(corr_x), abs(corr_y), abs(corr_z))))

    # Sort correlations by absolute value
    correlations.sort(key=lambda x: x[1], reverse=True)

    # Create a table of feature importances
    table_data = [{'Feature': feature, 'Importance': f"{importance:.3f}"} for feature, importance in correlations]

    return html.Div([
        html.H3("Feature Importance", style={'font-family': 'Montserrat', 'color': '#5B9BFF', 'font-weight': '700'}),
        html.P("The table below shows the correlation between each feature and the t-SNE coordinates. Higher values indicate stronger influence on player positioning."),
        dash_table.DataTable(
            data=table_data,
            columns=[{'name': i, 'id': i} for i in ['Feature', 'Importance']],
            style_cell={'textAlign': 'left'},
            style_header={
                'backgroundColor': 'rgb(230, 230, 230)',
                'fontWeight': 'bold'
            },
            style_data_conditional=[
                {
                    'if': {'row_index': 'odd'},
                    'backgroundColor': 'rgb(248, 248, 248)'
                }
            ]
        )
    ])

# Add a new callback for team feature importance
@app.callback(
    Output('team-feature-importance', 'children'),
    [Input('filtered-team-data', 'data')]
)
def update_team_feature_importance(filtered_data):
    global feature_columns
    # Convert the filtered data back to a dataframe
    filtered_df = pd.DataFrame(filtered_data)

    if filtered_df.empty:
        return html.Div("No data available for team feature importance calculation.")

    # Extract t-SNE coordinates from the filtered dataframe
    x = filtered_df['x']
    y = filtered_df['y']
    z = filtered_df['z']

    # Calculate correlations between original features and t-SNE coordinates
    feature_columns = ['pass_ratio', 'shot_ratio', 'ball_recovery_ratio', 'duel_ratio', 'interception_ratio',
                       'goal_ratio', 'pressure_ratio', 'dribble_ratio', 'foul_committed_ratio', 'foul_won_ratio',
                       'carry_ratio', 'dispossessed_ratio', 'clearance_ratio', 'block_ratio']
    
    correlations = []
    for feature in feature_columns:
        corr_x, _ = spearmanr(filtered_df[feature], x)
        corr_y, _ = spearmanr(filtered_df[feature], y)
        corr_z, _ = spearmanr(filtered_df[feature], z)
        correlations.append((feature, max(abs(corr_x), abs(corr_y), abs(corr_z))))

    # Sort correlations by absolute value
    correlations.sort(key=lambda x: x[1], reverse=True)

    # Create a table of feature importances
    table_data = [{'Feature': feature, 'Importance': f"{importance:.3f}"} for feature, importance in correlations]

    return html.Div([
        html.H3("Team Feature Importance", style={'font-family': 'Montserrat', 'color': '#5B9BFF', 'font-weight': '700'}),
        html.P("The table below shows the correlation between each team feature and the t-SNE coordinates. Higher values indicate stronger influence on team positioning."),
        dash_table.DataTable(
            data=table_data,
            columns=[{'name': i, 'id': i} for i in ['Feature', 'Importance']],
            style_cell={'textAlign': 'left'},
            style_header={
                'backgroundColor': 'rgb(230, 230, 230)',
                'fontWeight': 'bold'
            },
            style_data_conditional=[
                {
                    'if': {'row_index': 'odd'},
                    'backgroundColor': 'rgb(248, 248, 248)'
                }
            ]
        )
    ])

# Initialize Vertex AI
vertexai.init(project="awesome-advice-420021", location="us-central1")
model = GenerativeModel("gemini-1.5-pro")

# Generation config for Gemini
generation_config = {
    "max_output_tokens": 1024,
    "temperature": 0.1,
    "top_p": 0.1
}

def generate_insights(data, selected_items=None, item_type="team", perplexity=30, n_iter=1000):
    # Remove the lines that try to read perplexity and n_iter from the layout
    
    # Prepare the data for Gemini
    data_str = json.dumps(data, indent=2)
    
    context = f"""
    This data represents {item_type}s in a 3D space generated by t-SNE (t-Distributed Stochastic Neighbor Embedding).
    The t-SNE algorithm was run with perplexity {perplexity} and {n_iter} iterations.
    
    The x, y, and z coordinates represent the position of each {item_type} in this 3D space.
    {item_type.capitalize()}s that are closer together in this space have more similar playing styles based on the provided metrics.
    
    The other metrics are ratios representing the proportion of a {item_type}'s actions dedicated to that specific activity.
    For example, pass_ratio = passes made / total actions.
    
    Please analyze this data considering the following:
    1. Identify any clusters of {item_type}s with similar playing styles.
    2. Highlight any outliers and explain what makes them unique.
    3. Interpret the importance of different metrics in determining the {item_type}s' positions in the 3D space.
    4. If specific {item_type}s are selected, focus on explaining their position relative to others.

    Format your response in HTML, using appropriate tags for headings, paragraphs, lists, and emphasis.
    Use <h2 style="font-family: 'Montserrat'; color: '#5B9BFF';"> for main sections, <h3 style="font-family: 'Montserrat'; color: '#4285F4';"> for subsections, <p> for paragraphs, <ul> and <li> for unordered lists, and <strong> for emphasis.
    """
    
    if selected_items:
        selected_str = ", ".join(selected_items)
        prompt = f"{context}\n\nFocus on explaining the differences between the selected {item_type}s: {selected_str}. What makes them stand out from others?"
    else:
        prompt = f"{context}\n\nExplain why some {item_type}s are clustered together while others are far apart. Highlight any interesting patterns or outliers."
    
    prompt += f"\n\nData:\n{data_str}"
    
    # Generate insights using Gemini
    response = model.generate_content(prompt, generation_config=generation_config)
    
    # Add a small delay to show the loading animation
    time.sleep(1)
    
    return response.text

@app.callback(
    [Output('player-select', 'options', allow_duplicate=True),
     Output('player-select', 'value'),
     Output('position-filter', 'options', allow_duplicate=True),
     Output('position-filter', 'value')],
    [Input('team-select', 'value')],
    prevent_initial_call=True
)
def update_player_position_options(selected_teams):
    if not selected_teams:
        return [], [], [], []
    
    filtered_players = player_df[player_df['team_name'].isin(selected_teams)]['player_name'].unique()
    player_options = [{'label': player, 'value': player} for player in sorted(filtered_players)]
    
    filtered_positions = player_df[player_df['team_name'].isin(selected_teams)]['position'].unique()
    position_options = [{'label': pos, 'value': pos} for pos in sorted(filtered_positions)]
    
    return player_options, [], position_options, []  # Reset the selected players and positions when teams change

@app.callback(
    [Output('player-view-container', 'style'),
     Output('player-view-message', 'children'),
     Output('player-view-message', 'style')],
    [Input('team-select', 'value')]
)
def toggle_player_view(selected_teams):
    if not selected_teams:
        return {'display': 'none'}, html.Div([
            html.H2("Player View", style={'font-family': 'Montserrat', 'color': '#5B9BFF', 'font-weight': '700'}),
            html.P("Please select one or more teams to view player data.", 
                   style={'font-family': 'Inter', 'font-size': '18px', 'text-align': 'center'})
        ]), {'display': 'block'}
    else:
        return {'display': 'block'}, None, {'display': 'none'}

if __name__ == '__main__':
    app.run_server(debug=True)
